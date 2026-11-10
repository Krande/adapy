// Billboards (viewport/api/billboards.ts) on the three.js viewport: each one a THREE.Sprite in a group
// of the scene, drawn over the model on the overlay layer, as the type icons and glyph overlays are.
import * as THREE from "three";

import {requestRender} from "@/state/perfStore";
import {getViewerRuntime} from "@/state/viewerRuntime";
import {clipWithModel} from "@/utils/scene/section_clipping";
import {anchorClipVertexShader, type Billboard, type BillboardLayer} from "./billboards";

/** Layer 1: drawn by the camera, not tested by the scene's own picker. */
const OVERLAY_LAYER = 1;
const DEFAULT_ORDER = 9995;

/** One image's texture and the materials drawn with it, shared by every billboard that shows it. */
interface SharedImage {
    texture: THREE.CanvasTexture;
    aspect: number;
    world: THREE.SpriteMaterial | null;
    screen: THREE.SpriteMaterial | null;
    /** The same two for billboards the section planes cut: the planes are set on the material. */
    worldClipped: THREE.SpriteMaterial | null;
    screenClipped: THREE.SpriteMaterial | null;
    uses: number;
}

// One function for every clipped billboard: three keys compiled programs by the text of
// `onBeforeCompile`, so a shared one keeps them all on one program.
function anchorClip(shader: {vertexShader: string}): void {
    shader.vertexShader = anchorClipVertexShader(shader.vertexShader);
}

export function createThreeBillboardLayer(scene: THREE.Scene, name: string): BillboardLayer {
    const group = new THREE.Group();
    group.name = name;
    group.userData.__excludeFromFit = true;
    scene.add(group);
    const images = new Map<string, SharedImage>();
    const sprites = new Map<number, {sprite: THREE.Sprite; key: string}>();
    let nextHandle = 1;

    const acquire = (b: Billboard): SharedImage => {
        let shared = images.get(b.image.key);
        if (!shared) {
            const canvas = b.image.draw();
            const texture = new THREE.CanvasTexture(canvas);
            texture.anisotropy = 4;
            if (!b.image.untagged) texture.colorSpace = THREE.SRGBColorSpace;
            if (b.image.crisp) {
                texture.anisotropy = 1;
                texture.minFilter = THREE.LinearFilter;
            }
            shared = {
                texture, aspect: canvas.width / Math.max(1, canvas.height),
                world: null, screen: null, worldClipped: null, screenClipped: null, uses: 0,
            };
            images.set(b.image.key, shared);
        }
        shared.uses++;
        return shared;
    };
    const release = (key: string): void => {
        const shared = images.get(key);
        if (!shared || --shared.uses > 0) return;
        shared.world?.dispose();
        shared.screen?.dispose();
        shared.worldClipped?.dispose();
        shared.screenClipped?.dispose();
        shared.texture.dispose();
        images.delete(key);
    };
    const materialOf = (shared: SharedImage, sizing: Billboard["sizing"], clipped: boolean): THREE.SpriteMaterial => {
        const slot = clipped ? (sizing === "world" ? "worldClipped" : "screenClipped") : sizing;
        let material = shared[slot];
        if (!material) {
            material = shared[slot] = new THREE.SpriteMaterial({
                map: shared.texture,
                transparent: true,
                depthTest: false,
                depthWrite: false,
                sizeAttenuation: sizing === "world",
            });
            if (clipped) material.onBeforeCompile = anchorClip;
        }
        return material;
    };

    const place = (sprite: THREE.Sprite, b: Billboard, shared: SharedImage): void => {
        const material = materialOf(shared, b.sizing, !!b.clipped);
        if (sprite.material !== material) {
            sprite.material = material;
            // The section planes are put on whatever is tagged when they change, and on this one
            // now. Only when the material is another one: a billboard that is merely moved must
            // not have its shader compiled again.
            if (b.clipped) clipWithModel(sprite);
            else sprite.userData.__clipWithModel = false;
        }
        sprite.position.set(b.position[0], b.position[1], b.position[2]);
        sprite.center.set(b.anchor?.[0] ?? 0.5, b.anchor?.[1] ?? 0.5);
        sprite.renderOrder = b.order ?? DEFAULT_ORDER;
        sprite.userData.tag = b.tag;
        if (b.sizing === "world") {
            sprite.scale.set(b.height * shared.aspect, b.height, 1);
            sprite.onBeforeRender = noHook;
            return;
        }
        // A constant size on screen: without size attenuation a sprite's scale is a fraction of the
        // view's height (times the projection's focal term), so it follows the canvas' CSS height.
        const {height} = b;
        const {aspect} = shared;
        sprite.onBeforeRender = (renderer, _scene, camera) => {
            const cssHeight = Math.max(1, renderer.domElement.clientHeight);
            const s = (2 * height) / (cssHeight * camera.projectionMatrix.elements[5]);
            sprite.scale.set(s * aspect, s, 1);
        };
    };

    const dropSprite = (handle: number): void => {
        const entry = sprites.get(handle);
        if (!entry) return;
        group.remove(entry.sprite);
        release(entry.key);
        sprites.delete(handle);
    };

    return {
        add: (b) => {
            const sprite = new THREE.Sprite();
            sprite.layers.set(OVERLAY_LAYER);
            place(sprite, b, acquire(b));
            group.add(sprite);
            const handle = nextHandle++;
            sprites.set(handle, {sprite, key: b.image.key});
            requestRender();
            return handle;
        },
        update: (handle, b) => {
            const entry = sprites.get(handle);
            if (!entry) return;
            // The new image is held before the old one is let go, so an unchanged image is not redrawn.
            const shared = acquire(b);
            release(entry.key);
            entry.key = b.image.key;
            place(entry.sprite, b, shared);
            requestRender();
        },
        remove: (handle) => {
            dropSprite(handle);
            requestRender();
        },
        clear: () => {
            for (const handle of [...sprites.keys()]) dropSprite(handle);
            requestRender();
        },
        onClick: (listener) => {
            // The billboards are on the overlay layer, so a raycaster of their own is needed: the scene's
            // picker only tests layer 0.
            const raycaster = new THREE.Raycaster();
            raycaster.layers.set(OVERLAY_LAYER);
            const pointer = new THREE.Vector2();
            const onClick = (ev: MouseEvent) => {
                const el = getViewerRuntime().renderer.current?.domElement;
                const cam = getViewerRuntime().camera.current;
                if (!el || !cam) return;
                const rect = el.getBoundingClientRect();
                pointer.x = ((ev.clientX - rect.left) / rect.width) * 2 - 1;
                pointer.y = -((ev.clientY - rect.top) / rect.height) * 2 + 1;
                raycaster.setFromCamera(pointer, cam);
                const hits = raycaster.intersectObjects(group.children, false);
                if (hits.length) listener(hits.map((h) => h.object.userData.tag as unknown), ev);
            };
            const target = getViewerRuntime().renderer.current?.domElement ?? null;
            target?.addEventListener("click", onClick);
            return () => target?.removeEventListener("click", onClick);
        },
        dispose: () => {
            for (const handle of [...sprites.keys()]) dropSprite(handle);
            scene.remove(group);
            requestRender();
        },
    };
}

const noHook = (): void => {};
