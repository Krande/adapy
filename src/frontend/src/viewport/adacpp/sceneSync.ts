// Mirrors the three.js scene into the adacpp renderer: geometry is uploaded once per buffer version,
// instance state (transform, colour, flags, morph influence) and per-range state (hidden, selected)
// are pushed as they change. Everything three.js would draw on the camera's layers is mirrored, except
// what the renderer does itself (section caps) and what draws nothing visible (stencil-only helpers).
import * as THREE from "three";
import {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {selectedMaterial} from "@/utils/default_materials";
import {lightIntensities, useAppearanceStore} from "@/state/appearanceStore";
import {resultDrawnFlat} from "@/utils/scene/fea/threeResultLook";
import {isOrthographic} from "@/utils/scene/orthographicProjection";
import {quadMatrix} from "@/viewport/native/nativeImages";
import {InstanceFlag, NO_TEXTURE, SegmentFlag, SpriteFlag, Topology, type AdacppModule, type WebRendererApi} from "./types";

type SegmentKind = "batched" | "groups" | "edges" | "none";

interface Entry {
    obj: THREE.Object3D;
    signature: string;
    geometry: number;
    instances: number[];
    topology: number;
    segmentKind: SegmentKind;
    segmentIds: ReadonlyArray<string> | null; // batched: range id per segment
    segmentRangeIdx: Int32Array | null; // edges: rangeId per segment
    stateVersion: number;
    // Per-vertex data that changes in place, tracked by identity AND version: applyField installs a
    // fresh morph BufferAttribute on every FEA step, which starts at the version of the one it
    // replaces, so the version alone cannot tell the swap apart.
    colorAttr: object | undefined;
    colorVersion: number;
    morphAttr: object | undefined;
    morphVersion: number;
    seen: number;
}

type AnyMaterial = THREE.Material & {
    color?: THREE.Color;
    vertexColors?: boolean;
    flatShading?: boolean;
    uniforms?: Record<string, {value: unknown}>;
};

function attrVersion(a: THREE.BufferAttribute | THREE.InterleavedBufferAttribute | undefined): number {
    if (!a) return -1;
    return (a as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute
        ? (a as THREE.InterleavedBufferAttribute).data.version
        : (a as THREE.BufferAttribute).version;
}

// Buffer identity for the upload signature: a swapped-in attribute (an element field's
// element-local expansion) can carry the same version as the one it replaced.
const bufferIds = new WeakMap<object, number>();
let nextBufferId = 0;
function bufferId(a: object | null | undefined): number {
    if (!a) return -1;
    let id = bufferIds.get(a);
    if (id === undefined) {
        id = nextBufferId++;
        bufferIds.set(a, id);
    }
    return id;
}

/** Any position-like attribute as a tightly packed Float32Array of `size` components. */
function floats(a: THREE.BufferAttribute | THREE.InterleavedBufferAttribute, size = 3): Float32Array {
    const plain = a as THREE.BufferAttribute;
    if (!(a as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute && plain.array instanceof Float32Array &&
        a.itemSize === size && !a.normalized && plain.array.length === a.count * size) {
        return plain.array;
    }
    const out = new Float32Array(a.count * size);
    for (let i = 0; i < a.count; i++) {
        out[i * size] = a.getX(i);
        if (size > 1) out[i * size + 1] = a.getY(i);
        if (size > 2) out[i * size + 2] = a.getZ(i);
    }
    return out;
}

/** Vertex colours as packed linear RGBA8 (three.js treats vertex colours as linear), into `out` from
 *  `offset` (a wasm heap view, so a colour update allocates nothing on the JS side). */
function packColorsInto(a: THREE.BufferAttribute | THREE.InterleavedBufferAttribute, out: Uint32Array, offset = 0): void {
    const q = (v: number) => Math.max(0, Math.min(255, Math.round(v * 255)));
    const hasAlpha = a.itemSize >= 4;
    for (let i = 0; i < a.count; i++) {
        out[offset + i] =
            (q(a.getX(i)) | (q(a.getY(i)) << 8) | (q(a.getZ(i)) << 16) | (q(hasAlpha ? a.getW(i) : 1) << 24)) >>> 0;
    }
}

function packedColors(a: THREE.BufferAttribute | THREE.InterleavedBufferAttribute): Uint32Array {
    const out = new Uint32Array(a.count);
    packColorsInto(a, out);
    return out;
}

function packColor(c: THREE.Color, alpha = 1): number {
    const q = (v: number) => Math.max(0, Math.min(255, Math.round(v * 255)));
    return (q(c.r) | (q(c.g) << 8) | (q(c.b) << 16) | (q(alpha) << 24)) >>> 0;
}

function baseMaterial(obj: THREE.Object3D): AnyMaterial | undefined {
    if (obj instanceof CustomBatchedMesh) return obj.originalMaterial as AnyMaterial;
    const m = (obj as THREE.Mesh).material as AnyMaterial | AnyMaterial[] | undefined;
    return Array.isArray(m) ? m[0] : m;
}

function materialColor(m: AnyMaterial | undefined, fallback: number): THREE.Color {
    if (m?.color instanceof THREE.Color) return m.color;
    const u = m?.uniforms;
    for (const key of ["color", "diffuse", "uColor"]) {
        const v = u?.[key]?.value;
        if (v instanceof THREE.Color) return v;
    }
    return new THREE.Color(fallback);
}

function isUnlit(m: AnyMaterial | undefined): boolean {
    if (!m) return false;
    const t = m as unknown as Record<string, boolean>;
    // A painted FEA result is drawn flat, as three.js draws it (utils/scene/fea/threeResultLook.ts).
    return !!(t.isMeshBasicMaterial || t.isLineBasicMaterial || t.isLineDashedMaterial || t.isPointsMaterial ||
        t.isShaderMaterial || t.isLineMaterial) || resultDrawnFlat(m);
}

/** The layer sprites are put on (as well as their own) for the three.js pass that draws them over the WebGPU
 *  image: the renderer has no textures, and sprites are canvas-textured labels and markers. */
export const SPRITE_LAYER = 31;

/** Dash and gap lengths in line-distance units, for LineMaterial (fat lines) and LineDashedMaterial; [0, 0]
 *  = solid. Both scale the distance (dashScale / scale), which is the same as dividing the lengths. */
function dashOf(mat: THREE.Material | undefined): [number, number] {
    const m = mat as unknown as {
        dashed?: boolean; isLineDashedMaterial?: boolean; isLineMaterial?: boolean;
        dashSize?: number; gapSize?: number; dashScale?: number; scale?: number;
    } | undefined;
    if (!m || !(m.isLineDashedMaterial || (m.isLineMaterial && m.dashed))) return [0, 0];
    const scale = (m.isLineMaterial ? m.dashScale : m.scale) || 1;
    return [(m.dashSize ?? 0) / scale, (m.gapSize ?? 0) / scale];
}
const MAX_CLIP_PLANES = 6;

/** RGBA8 pixels of a texture's image, top row first (what the renderer's addTexture takes). */
export type ReadPixels = (image: unknown) => {data: Uint8ClampedArray | Uint8Array; width: number; height: number} | null;

// three.js reads a texture without a colour space as linear and encodes the result for the screen; the
// renderer takes sRGB pixels. Encoding such a texture's pixels at upload gives the same picture.
const LINEAR_TO_SRGB = Uint8Array.from({length: 256}, (_, i) => {
    const c = i / 255;
    return Math.round(255 * (c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(c, 1 / 2.4) - 0.055));
});

let scratch: HTMLCanvasElement | null = null;
/** Through a 2D canvas: a sprite's map is a canvas, an image or a bitmap. */
const readPixelsWithCanvas: ReadPixels = (image) => {
    const src = image as (CanvasImageSource & {width: number; height: number}) | null | undefined;
    if (!src || !src.width || !src.height || typeof document === "undefined") return null;
    scratch ??= document.createElement("canvas");
    scratch.width = src.width;
    scratch.height = src.height;
    const ctx = scratch.getContext("2d", {willReadFrequently: true});
    if (!ctx) return null;
    ctx.clearRect(0, 0, src.width, src.height);
    ctx.drawImage(src, 0, 0);
    return ctx.getImageData(0, 0, src.width, src.height);
};

export class SceneSync {
    private readonly entries = new Map<string, Entry>();
    private readonly byInstance = new Map<number, Entry>();
    private frame = 0;
    private readonly matrixPtr: number;
    private readonly viewPtr: number;
    private readonly projPtr: number;
    private readonly bg = new THREE.Color();
    // The parts of WebGLRenderer that onBeforeRender hooks use (LineMaterial reads the size).
    private readonly hookRenderer = {
        width: 1,
        height: 1,
        pixelRatio: 1,
        getSize(target: THREE.Vector2) {
            return target.set(this.width, this.height);
        },
        getDrawingBufferSize(target: THREE.Vector2) {
            return target.set(this.width, this.height);
        },
        getPixelRatio(): number {
            return this.pixelRatio;
        },
        clearStencil: () => {},
    };
    lastSyncMs = 0;
    /** Points registered with the point picker are pickable in the renderer too (the `adacppPointPick`
     *  switch). Off, only model meshes are, so a mesh pick never lands on a point in front of it. */
    pickPoints = false;
    /** Sprites and through-the-model overlays are drawn by the renderer (the `adacppLabels` switch) instead of
     *  being left for the three.js pass. Cleared, with the reason in `nativeSpritesError`, if that fails. */
    nativeSprites = false;
    nativeSpritesError: string | null = null;
    private readonly sprites = new Map<string, {id: number; seen: number}>();
    private readonly textures = new Map<string, {id: number; version: number; seen: number}>();
    private forceOnTop = false;
    /** Sprites the last sync found on the camera's layers; the caller draws them with three.js. */
    spriteCount = 0;
    private pixelRatio = 1;
    // Section planes: three.js clips per material; the renderer has one set, the union of this frame's.
    private readonly framePlanes = new Set<THREE.Plane>();
    private planesKey = "";
    private readonly planesPtr: number;
    // Section caps: three.js fills a cut with stencil helper meshes and one cap quad per plane; the
    // renderer fills it itself, so those helpers are not mirrored. A cap quad seen this frame switches
    // the renderer's caps on, in its colour.
    private frameCapColor: THREE.Color | null = null;
    private capsKey = "";

    constructor(
        private readonly module: AdacppModule,
        private readonly r: WebRendererApi,
        private readonly readPixels: ReadPixels = readPixelsWithCanvas,
    ) {
        this.matrixPtr = module._malloc(64);
        this.viewPtr = module._malloc(64);
        this.projPtr = module._malloc(64);
        this.planesPtr = module._malloc(MAX_CLIP_PLANES * 16);
        // The lights are the appearance settings', as the three.js lights are (setupLights.ts):
        // set when they change, not each frame.
        const light = () => {
            const {ambient, headlight} = lightIntensities();
            r.setLighting(ambient, headlight);
        };
        light();
        this.unlight = useAppearanceStore.subscribe((s, prev) => {
            if (s.brightness !== prev.brightness || s.ambientShare !== prev.ambientShare) light();
        });
    }

    private readonly unlight: () => void;

    dispose(): void {
        this.unlight();
        this.entries.clear();
        this.byInstance.clear();
        this.module._free(this.matrixPtr);
        this.module._free(this.viewPtr);
        this.module._free(this.projPtr);
        this.module._free(this.planesPtr);
    }

    /** `width` x `height`: the drawing-buffer size, reported to onBeforeRender hooks that ask the renderer. */
    sync(scene: THREE.Scene, camera: THREE.Camera, width = 1, height = 1, pixelRatio = 1): void {
        this.pixelRatio = pixelRatio;
        const t0 = performance.now();
        this.frame++;
        this.spriteCount = 0;
        this.framePlanes.clear();
        this.frameCapColor = null;
        scene.updateMatrixWorld();
        if (camera.parent === null) camera.updateMatrixWorld();

        if (scene.background instanceof THREE.Color) {
            this.bg.copy(scene.background).convertLinearToSRGB();
            this.r.setBackground(this.bg.r, this.bg.g, this.bg.b);
        }

        scene.traverseVisible((obj) => {
            if (!camera.layers.test(obj.layers)) return;
            if (obj.parent instanceof CustomBatchedMesh && obj.parent.isHighlightOverlay(obj)) {
                this.syncFaceHighlight(obj.parent, obj);
                return;
            }
            const o = obj as unknown as Record<string, boolean>;
            // Sprites, and what asks to be drawn over the model (a plugin's lines `throughModel`), go to
            // the three.js pass over the WebGPU image.
            if (o.isSprite || obj.userData.__overlayPass) {
                if (!this.nativeSprites) {
                    obj.layers.enable(SPRITE_LAYER);
                    this.spriteCount++;
                    return;
                }
                if (o.isSprite) {
                    this.syncSprite(obj as THREE.Sprite);
                    return;
                }
                // Not a sprite: mirrored like any other object, but over the model.
                this.forceOnTop = true;
            } else {
                this.forceOnTop = false;
            }
            if (!(o.isMesh || o.isLineSegments || o.isLine || o.isPoints)) return;
            // An image lying in the world (viewport/api/threeImages.ts) is a quad of the renderer.
            if (obj.userData.__worldImage && this.syncImage(obj as THREE.Mesh)) return;
            if (obj.userData.__sectionCap) {
                this.frameCapColor = materialColor(baseMaterial(obj), 0x888888);
                return;
            }
            // Nothing to see: what only writes depth or stencil (the caps' stencil passes).
            if (baseMaterial(obj)?.colorWrite === false) return;
            // three.js calls onBeforeRender only on objects it draws, and here it draws nothing. Scene
            // code relies on the hook for per-frame state: FEA overlays (element wireframe, beam lines,
            // result points) copy the mesh's morph influence there, the selection overlay follows the
            // morph, handles scale with the camera. Run it as three would, then mirror the result.
            this.hookRenderer.width = width;
            this.hookRenderer.height = height;
            this.hookRenderer.pixelRatio = pixelRatio;
            const mesh = obj as THREE.Mesh;
            const hookMaterial = baseMaterial(obj) as THREE.Material;
            obj.onBeforeRender(this.hookRenderer as unknown as THREE.WebGLRenderer, scene, camera, mesh.geometry,
                hookMaterial, null as unknown as THREE.Group);
            // three.js also calls the material's hook, right after the object's.
            hookMaterial?.onBeforeRender?.(this.hookRenderer as unknown as THREE.WebGLRenderer, scene, camera,
                mesh.geometry, obj, null as unknown as THREE.Group);
            this.syncObject(obj);
        });

        for (const [key, e] of this.entries) {
            if (e.seen !== this.frame) {
                this.drop(e);
                this.entries.delete(key);
            }
        }
        this.dropUnseenSprites();

        this.pushClipPlanes();
        this.pushSectionCaps();
        const heap = this.module.HEAPF32;
        heap.set(camera.matrixWorldInverse.elements, this.viewPtr >> 2);
        heap.set(camera.projectionMatrix.elements, this.projPtr >> 2);
        // The three.js viewport's camera may draw orthographically (orthographicProjection.ts).
        this.r.setCamera(this.viewPtr, this.projPtr, isOrthographic(camera));
        this.lastSyncMs = performance.now() - t0;
    }

    private pushClipPlanes(): void {
        const planes = [...this.framePlanes].slice(0, MAX_CLIP_PLANES);
        const key = planes.map((p) => `${p.normal.x},${p.normal.y},${p.normal.z},${p.constant}`).join(";");
        if (key === this.planesKey) return;
        this.planesKey = key;
        const heap = this.module.HEAPF32;
        planes.forEach((p, i) => heap.set([p.normal.x, p.normal.y, p.normal.z, p.constant], (this.planesPtr >> 2) + i * 4));
        this.r.setClipPlanes(this.planesPtr, planes.length);
    }

    private pushSectionCaps(): void {
        const c = this.frameCapColor;
        const key = c ? `${c.r},${c.g},${c.b}` : "";
        if (key === this.capsKey) return;
        this.capsKey = key;
        // Optional calls: a renderer from before caps existed draws the cut hollow, as it always did.
        this.r.setSectionCaps?.(!!c);
        if (c) this.r.setSectionCapColor?.(c.r, c.g, c.b, 1);
    }

    /** The scene object an instance draws, and which of its segments' range ids is which. */
    entryForInstance(instance: number): {obj: THREE.Object3D; segmentIds: ReadonlyArray<string> | null} | null {
        const e = this.byInstance.get(instance);
        return e ? {obj: e.obj, segmentIds: e.segmentIds} : null;
    }

    private drop(e: Entry): void {
        for (const id of e.instances) {
            this.r.removeInstance(id);
            this.byInstance.delete(id);
        }
        this.r.removeGeometry(e.geometry);
    }

    /** A three.js Sprite as a renderer sprite: its map's pixels as a texture (uploaded again when the
     *  texture's version moves, as a relabelled canvas does), placed and sized as three.js would. */
    private syncSprite(sprite: THREE.Sprite): void {
        const mat = sprite.material;
        if (!mat.visible) return;
        try {
            const texture = this.syncTexture(mat.map);
            let entry = this.sprites.get(sprite.uuid);
            if (!entry) {
                entry = {id: this.r.addSprite(), seen: 0};
                this.sprites.set(sprite.uuid, entry);
            }
            entry.seen = this.frame;
            const m = sprite.matrixWorld.elements;
            let flags = 0;
            if (mat.sizeAttenuation) flags |= SpriteFlag.SizeAttenuation;
            if (mat.depthTest) flags |= SpriteFlag.DepthTest;
            if (mat.clippingPlanes?.length) {
                flags |= SpriteFlag.Clipped;
                for (const p of mat.clippingPlanes) this.framePlanes.add(p);
            }
            this.r.setSprite(
                entry.id, texture, m[12], m[13], m[14], Math.hypot(m[0], m[1], m[2]), Math.hypot(m[4], m[5], m[6]),
                sprite.center.x, sprite.center.y, mat.rotation, mat.color.r, mat.color.g, mat.color.b, mat.opacity,
                flags, sprite.renderOrder,
            );
        } catch (err) {
            // Back to the three.js pass for good: a label drawn the old way beats no label.
            this.nativeSprites = false;
            this.nativeSpritesError = err instanceof Error ? err.message : String(err);
            this.dropUnseenSprites(true);
            sprite.layers.enable(SPRITE_LAYER);
            this.spriteCount++;
        }
    }

    /** An image lying in the world as a renderer quad: its texture's bytes as a texture without a
     *  mip chain (written again when the texture's version moves), placed on the mesh's rectangle
     *  with the texture's first row at the rectangle's first corner. False when the renderer module
     *  has no quads: the mesh is then mirrored as any other. */
    private syncImage(mesh: THREE.Mesh): boolean {
        if (typeof this.r.addQuad !== "function" || typeof this.r.addPlainTexture !== "function") return false;
        const mat = mesh.material as THREE.MeshBasicMaterial;
        const map = mat.map as THREE.DataTexture | null;
        const image = map?.image as {data?: Uint8Array; width: number; height: number} | undefined;
        if (!map || !image?.data) return false;
        let texture = this.textures.get(map.uuid);
        if (!texture || texture.version !== map.version) {
            const p = this.module._malloc(image.data.length);
            this.module.HEAPU8.set(image.data, p);
            try {
                if (texture) this.r.updateTexture(texture.id, p, image.width, image.height);
                else texture = {id: this.r.addPlainTexture(p, image.width, image.height), version: 0, seen: 0};
            } finally {
                this.module._free(p);
            }
            texture.version = map.version;
            this.textures.set(map.uuid, texture);
        }
        texture.seen = this.frame;
        let quad = this.quads.get(mesh.uuid);
        if (!quad) this.quads.set(mesh.uuid, (quad = {id: this.r.addQuad(), seen: 0}));
        quad.seen = this.frame;
        // The mesh's four corners are the rectangle: origin, +u, +u+v, +v.
        const corners = (mesh.geometry.getAttribute("position") as THREE.BufferAttribute).array as Float32Array;
        const {origin, u, v} = this.imageRect;
        for (let k = 0; k < 3; k++) {
            origin[k] = corners[k];
            u[k] = corners[3 + k] - corners[k];
            v[k] = corners[9 + k] - corners[k];
        }
        let flags = mat.depthTest ? InstanceFlag.DepthOffset : InstanceFlag.OnTop;
        if (map.magFilter === THREE.NearestFilter) flags |= InstanceFlag.Nearest;
        if (mat.clippingPlanes?.length) {
            flags |= InstanceFlag.Clipped;
            for (const plane of mat.clippingPlanes) this.framePlanes.add(plane);
        }
        quadMatrix(this.imageRect, mesh.matrixWorld.elements, this.imageMatrix);
        this.module.HEAPF32.set(this.imageMatrix, this.matrixPtr >> 2);
        this.r.setQuad(quad.id, texture.id, this.matrixPtr, 1, 1, 1, mat.opacity, flags, mesh.renderOrder);
        return true;
    }

    private readonly quads = new Map<string, {id: number; seen: number}>();
    private readonly imageRect = {origin: [0, 0, 0] as [number, number, number], u: [0, 0, 0] as [number, number, number], v: [0, 0, 0] as [number, number, number]};
    private readonly imageMatrix = new Float32Array(16);

    private syncTexture(map: THREE.Texture | null): number {
        if (!map?.image) return NO_TEXTURE;
        let t = this.textures.get(map.uuid);
        if (t && t.version === map.version) {
            t.seen = this.frame;
            return t.id;
        }
        const pixels = this.readPixels(map.image);
        if (!pixels) return t ? ((t.seen = this.frame), t.id) : NO_TEXTURE;
        const p = this.module._malloc(pixels.data.length);
        this.module.HEAPU8.set(pixels.data, p);
        if (map.colorSpace !== THREE.SRGBColorSpace) {
            const heap = this.module.HEAPU8;
            for (let i = p, end = p + pixels.data.length; i < end; i += 4) {
                heap[i] = LINEAR_TO_SRGB[heap[i]];
                heap[i + 1] = LINEAR_TO_SRGB[heap[i + 1]];
                heap[i + 2] = LINEAR_TO_SRGB[heap[i + 2]];
            }
        }
        try {
            if (t) this.r.updateTexture(t.id, p, pixels.width, pixels.height);
            else t = {id: this.r.addTexture(p, pixels.width, pixels.height), version: 0, seen: 0};
        } finally {
            this.module._free(p);
        }
        t.version = map.version;
        t.seen = this.frame;
        this.textures.set(map.uuid, t);
        return t.id;
    }

    /** Sprites and textures no longer in the scene (`all`: every one, when handing back to three.js). */
    private dropUnseenSprites(all = false): void {
        for (const [key, s] of this.sprites) {
            if (all || s.seen !== this.frame) {
                this.r.removeSprite(s.id);
                this.sprites.delete(key);
            }
        }
        for (const [key, q] of this.quads) {
            if (all || q.seen !== this.frame) {
                this.r.removeQuad(q.id);
                this.quads.delete(key);
            }
        }
        for (const [key, t] of this.textures) {
            if (all || t.seen !== this.frame) {
                this.r.removeTexture(t.id);
                this.textures.delete(key);
            }
        }
    }

    /** The highlighted face of a model mesh (Faces picking). three.js draws it with an overlay that shares
     *  the whole model's buffers and shows one index range; mirroring that would upload the model again on
     *  every click, so only the face's own triangles are uploaded, as a small geometry of their own. */
    private syncFaceHighlight(mesh: CustomBatchedMesh, obj: THREE.Object3D): void {
        const face = mesh.faceHighlightRange();
        if (!face || face.overlay !== obj) return;
        const geometry = face.overlay.geometry;
        const position = geometry.attributes.position;
        if (!position) return;
        const index = geometry.index;
        const morphAttr = geometry.morphAttributes.position?.[0];
        const signature = `face:${geometry.uuid}:${bufferId(position)}:${attrVersion(position)}:${bufferId(index)}:` +
            `${face.start}:${face.count}:${bufferId(morphAttr)}:${attrVersion(morphAttr)}`;
        let e = this.entries.get(obj.uuid);
        if (e && e.signature !== signature) {
            this.drop(e);
            this.entries.delete(obj.uuid);
            e = undefined;
        }
        if (!e) {
            // Only the vertices the face uses, renumbered from 0.
            const local = new Map<number, number>();
            const indices = new Uint32Array(face.count);
            const positions: number[] = [];
            const morph: number[] = [];
            for (let i = 0; i < face.count; i++) {
                const v = index ? index.getX(face.start + i) : face.start + i;
                let k = local.get(v);
                if (k === undefined) {
                    k = local.size;
                    local.set(v, k);
                    positions.push(position.getX(v), position.getY(v), position.getZ(v));
                    if (morphAttr) morph.push(morphAttr.getX(v), morphAttr.getY(v), morphAttr.getZ(v));
                }
                indices[i] = k;
            }
            const M = this.module;
            const pPos = M._malloc(positions.length * 4), pIdx = M._malloc(indices.byteLength);
            const pMorph = morph.length ? M._malloc(morph.length * 4) : 0;
            M.HEAPF32.set(positions, pPos >> 2);
            M.HEAPU32.set(indices, pIdx >> 2);
            if (pMorph) M.HEAPF32.set(morph, pMorph >> 2);
            let geom: number;
            try {
                geom = this.r.addGeometry(Topology.Triangles, pPos, local.size, 0, 0, pMorph, pIdx, indices.length, 0, 0, 0);
            } finally {
                M._free(pPos);
                M._free(pIdx);
                if (pMorph) M._free(pMorph);
            }
            e = {
                obj, signature, geometry: geom, instances: [this.r.addInstance(geom)], topology: Topology.Triangles,
                segmentKind: "none", segmentIds: null, segmentRangeIdx: null, stateVersion: -1, seen: 0,
                colorAttr: undefined, colorVersion: -1, morphAttr, morphVersion: attrVersion(morphAttr),
            };
            this.entries.set(obj.uuid, e);
            this.byInstance.set(e.instances[0], e);
        }
        e.seen = this.frame;
        const mats = face.overlay.material;
        const mat = (Array.isArray(mats) ? mats[0] : mats) as AnyMaterial;
        const color = materialColor(mat, 0xffffff);
        // In front of the surface it lies on, from either side, and never what a click lands on.
        let flags = InstanceFlag.DoubleSided | InstanceFlag.DepthOffset | InstanceFlag.NoPick;
        if (geometry.morphTargetsRelative) flags |= InstanceFlag.MorphRelative;
        const planes = (baseMaterial(mesh)?.clippingPlanes ?? mat.clippingPlanes) as THREE.Plane[] | null | undefined;
        if (planes?.length) {
            flags |= InstanceFlag.Clipped;
            for (const p of planes) this.framePlanes.add(p);
        }
        this.module.HEAPF32.set(obj.matrixWorld.elements, this.matrixPtr >> 2);
        // Matte, as three.js lights the selection material.
        this.r.setInstance(e.instances[0], this.matrixPtr, color.r, color.g, color.b, 1, flags,
            face.overlay.morphTargetInfluences?.[0] ?? 0, 0, 1, true, 1, 0, 0);
    }

    private syncObject(obj: THREE.Object3D): void {
        const o = obj as unknown as Record<string, boolean>;
        const geometry = (obj as THREE.Mesh).geometry as THREE.BufferGeometry | undefined;
        if (!geometry?.attributes) return;
        const mat = baseMaterial(obj);
        const fatLines = !!(o.isLineSegments2 || o.isLine2);
        const position = fatLines ? geometry.attributes.instanceStart : geometry.attributes.position;
        if (!position || position.count === 0) return;

        const topology = o.isPoints ? Topology.Points : (o.isLine || o.isLineSegments || fatLines) ? Topology.Lines : Topology.Triangles;
        const flat = topology === Topology.Triangles && !!mat?.flatShading;
        const groupsSig = obj instanceof CustomBatchedMesh ? "b" : `${geometry.groups.length}`;
        // BackSide draws the faces turned away: the renderer only culls back faces, so the winding is
        // reversed at upload instead (a PlaneHelper's fill flips between the two as its plane moves).
        const backSide = topology === Topology.Triangles && mat?.side === THREE.BackSide;
        const positionBuffer = (position as THREE.InterleavedBufferAttribute).isInterleavedBufferAttribute
            ? (position as THREE.InterleavedBufferAttribute).data
            : position;
        const signature = `${geometry.uuid}:${bufferId(positionBuffer)}:${attrVersion(position)}:` +
            `${bufferId(geometry.index)}:${geometry.index?.version ?? -1}:${topology}:${flat}:${groupsSig}:${backSide}`;

        let e = this.entries.get(obj.uuid);
        if (e && e.signature !== signature) {
            this.drop(e);
            this.entries.delete(obj.uuid);
            e = undefined;
        }
        if (!e) {
            const created = this.upload(obj, geometry, topology, flat, fatLines, backSide);
            if (!created) return;
            const morph0 = fatLines ? undefined : geometry.morphAttributes.position?.[0];
            e = {
                ...created, obj, signature, stateVersion: -1, seen: 0,
                colorAttr: geometry.attributes.color, colorVersion: attrVersion(geometry.attributes.color),
                morphAttr: morph0, morphVersion: attrVersion(morph0),
            };
            this.entries.set(obj.uuid, e);
            for (const id of e.instances) this.byInstance.set(id, e);
        }
        e.seen = this.frame;

        // Per-vertex data that changes in place (FEA field colours, warp).
        const colorAttr = geometry.attributes.color;
        if (colorAttr && !fatLines && (colorAttr !== e.colorAttr || attrVersion(colorAttr) !== e.colorVersion)) {
            e.colorAttr = colorAttr;
            e.colorVersion = attrVersion(colorAttr);
            // Every FEA repaint swaps in fresh colours: pack them straight into the wasm heap.
            const n = colorAttr.count;
            const p = this.module._malloc(n * 4);
            try {
                packColorsInto(colorAttr, this.module.HEAPU32, p >> 2);
                this.r.updateColors(e.geometry, p, n);
            } finally {
                this.module._free(p);
            }
        }
        const morphAttr = fatLines ? undefined : geometry.morphAttributes.position?.[0];
        if (morphAttr && (morphAttr !== e.morphAttr || attrVersion(morphAttr) !== e.morphVersion)) {
            e.morphAttr = morphAttr;
            e.morphVersion = attrVersion(morphAttr);
            this.withF32(floats(morphAttr), (p) => this.r.updateMorph(e!.geometry, p, morphAttr.count));
        }

        this.pushInstances(obj, e, mat, geometry);
        this.pushSegmentStates(obj, e);
    }

    private upload(
        obj: THREE.Object3D,
        geometry: THREE.BufferGeometry,
        topology: number,
        flat: boolean,
        fatLines: boolean,
        backSide = false,
    ): Pick<Entry, "geometry" | "instances" | "topology" | "segmentKind" | "segmentIds" | "segmentRangeIdx"> | null {
        let positions: Float32Array;
        let colors: Uint32Array | null = null;
        let indices: Uint32Array | null = null;
        let distances: Float32Array | null = null; // along the line, for dashes (computeLineDistances)
        if (fatLines) {
            // LineSegments2: one instance per segment, endpoints in instanceStart / instanceEnd.
            const s = geometry.attributes.instanceStart, en = geometry.attributes.instanceEnd;
            positions = new Float32Array(s.count * 6);
            for (let i = 0; i < s.count; i++) {
                positions.set([s.getX(i), s.getY(i), s.getZ(i), en.getX(i), en.getY(i), en.getZ(i)], i * 6);
            }
            const ds = geometry.attributes.instanceDistanceStart, de = geometry.attributes.instanceDistanceEnd;
            if (ds && de) {
                distances = new Float32Array(s.count * 2);
                for (let i = 0; i < s.count; i++) distances.set([ds.getX(i), de.getX(i)], i * 2);
            }
            const cs = geometry.attributes.instanceColorStart, ce = geometry.attributes.instanceColorEnd;
            if (cs && ce) {
                const a = packedColors(cs), b = packedColors(ce);
                colors = new Uint32Array(s.count * 2);
                for (let i = 0; i < s.count; i++) {
                    colors[i * 2] = a[i];
                    colors[i * 2 + 1] = b[i];
                }
            }
        } else {
            positions = floats(geometry.attributes.position);
            if (geometry.attributes.color) colors = packedColors(geometry.attributes.color);
            if (topology === Topology.Lines && geometry.attributes.lineDistance) {
                distances = floats(geometry.attributes.lineDistance);
            }
            if (geometry.index) {
                const ia = geometry.index.array;
                indices = ia instanceof Uint32Array ? ia : Uint32Array.from(ia as ArrayLike<number>);
            }
            if ((obj as THREE.Line).isLine && !(obj as THREE.LineSegments).isLineSegments) {
                // Line strip -> segment list.
                const n = indices ? indices.length : positions.length / 3;
                const strip = indices ?? Uint32Array.from({length: n}, (_, i) => i);
                const seg = new Uint32Array(Math.max(0, n - 1) * 2);
                for (let i = 0; i + 1 < n; i++) {
                    seg[i * 2] = strip[i];
                    seg[i * 2 + 1] = strip[i + 1];
                }
                indices = seg;
            }
        }
        if (backSide) {
            const src = indices ?? Uint32Array.from({length: positions.length / 3}, (_, i) => i);
            indices = new Uint32Array(src.length);
            for (let i = 0; i + 2 < src.length; i += 3) indices.set([src[i], src[i + 2], src[i + 1]], i);
        }
        const vertexCount = positions.length / 3;
        const normals = topology === Topology.Triangles && !flat && geometry.attributes.normal && !fatLines
            ? floats(geometry.attributes.normal) : null;
        const morphAttr = fatLines ? undefined : geometry.morphAttributes.position?.[0];
        const morph = morphAttr ? floats(morphAttr) : null;

        // Segments: the ranges per-object state applies to.
        let segmentKind: SegmentKind = "none";
        let segments: Uint32Array | null = null;
        let segmentIds: ReadonlyArray<string> | null = null;
        let segmentRangeIdx: Int32Array | null = null;
        const indexCount = indices ? indices.length : vertexCount;
        if (obj instanceof CustomBatchedMesh) {
            const segs = obj.getSortedSegments();
            const list: number[] = [];
            const ids: string[] = [];
            let cursor = 0;
            for (let i = 0; i < segs.ids.length; i++) {
                const start = segs.starts[i], count = segs.counts[i];
                if (start > cursor) { // gaps draw with the default material, like updateGroups does
                    list.push(cursor, start - cursor);
                    ids.push("");
                }
                list.push(start, count);
                ids.push(segs.ids[i]);
                cursor = Math.max(cursor, start + count);
            }
            if (cursor < indexCount) {
                list.push(cursor, indexCount - cursor);
                ids.push("");
            }
            segments = Uint32Array.from(list);
            segmentIds = ids;
            segmentKind = "batched";
        } else if (topology === Topology.Lines && geometry.attributes.rangeId && !indices &&
            (baseMaterial(obj)?.uniforms?.uVisibleTex)) {
            // CAD edge overlay: contiguous runs of the per-vertex rangeId.
            const rid = geometry.attributes.rangeId;
            const list: number[] = [];
            const idx: number[] = [];
            let start = 0;
            for (let i = 1; i <= rid.count; i++) {
                if (i === rid.count || rid.getX(i) !== rid.getX(start)) {
                    list.push(start, i - start);
                    idx.push(rid.getX(start));
                    start = i;
                }
            }
            segments = Uint32Array.from(list);
            segmentRangeIdx = Int32Array.from(idx);
            segmentKind = "edges";
        } else if (geometry.groups.length > 0 && Array.isArray((obj as THREE.Mesh).material)) {
            segments = Uint32Array.from(geometry.groups.flatMap((g) => [g.start, Math.min(g.count, indexCount - g.start)]));
            segmentKind = "groups";
        }

        const M = this.module;
        const alloc = (a: Float32Array | Uint32Array | null): number => {
            if (!a || a.length === 0) return 0;
            const p = M._malloc(a.byteLength);
            (a instanceof Float32Array ? M.HEAPF32 : M.HEAPU32).set(a, p >> 2);
            return p;
        };
        const ptrs = [positions, normals, colors, morph, indices, segments, distances].map(alloc);
        let geom: number;
        try {
            geom = this.r.addGeometry(
                topology, ptrs[0], vertexCount, ptrs[1], ptrs[2], ptrs[3], ptrs[4], indices ? indices.length : 0,
                ptrs[5], segments ? segments.length / 2 : 0, ptrs[6],
            );
        } finally {
            for (const p of ptrs) if (p) M._free(p);
        }

        const count = (obj as THREE.InstancedMesh).isInstancedMesh ? (obj as THREE.InstancedMesh).count : 1;
        const instances: number[] = [];
        for (let i = 0; i < count; i++) instances.push(this.r.addInstance(geom));
        return {geometry: geom, instances, topology, segmentKind, segmentIds, segmentRangeIdx};
    }

    private readonly tmpMatrix = new THREE.Matrix4();

    private pushInstances(obj: THREE.Object3D, e: Entry, mat: AnyMaterial | undefined, geometry: THREE.BufferGeometry) {
        const color = materialColor(mat, e.topology === Topology.Triangles ? 0xffffff : 0x000000);
        const opacity = mat?.opacity ?? 1;
        let flags = 0;
        const hasColors = !!geometry.attributes.color || !!geometry.attributes.instanceColorStart;
        if (hasColors && (mat?.vertexColors || (mat as unknown as {isShaderMaterial?: boolean})?.isShaderMaterial)) {
            flags |= InstanceFlag.VertexColors;
        }
        if (isUnlit(mat) || e.topology !== Topology.Triangles) flags |= InstanceFlag.Unlit;
        if (mat?.side === THREE.DoubleSide) flags |= InstanceFlag.DoubleSided;
        if (mat?.transparent && opacity < 1) {
            flags |= InstanceFlag.Transparent;
            // The renderer draws both sides of a transparent instance unless told the material is single-sided.
            if (mat.side !== THREE.DoubleSide) flags |= InstanceFlag.CullBack;
        }
        if (mat && !mat.visible) flags |= InstanceFlag.Hidden;
        if (geometry.morphTargetsRelative) flags |= InstanceFlag.MorphRelative;
        const planes = mat?.clippingPlanes;
        if (planes?.length) {
            flags |= InstanceFlag.Clipped;
            for (const p of planes) this.framePlanes.add(p);
            // A cut model mesh that is a closed solid gets its cut filled; an open shell (double-sided)
            // has no inside to fill. The same rule as the three.js caps (SectionPlanesController).
            if (obj instanceof CustomBatchedMesh && mat?.side !== THREE.DoubleSide) flags |= InstanceFlag.Capped;
        }
        // three.js' depthTest: false draws over whatever is there (transform gizmo handles).
        if (mat?.depthTest === false || this.forceOnTop) flags |= InstanceFlag.OnTop;
        // three.js' polygonOffset: negative pulls an overlay in front of the surface it lies on, positive
        // pushes a surface behind the edges drawn on it (FEA meshes). Without it, no bias, as in three.js.
        if (mat?.polygonOffset && (mat.polygonOffsetFactor < 0 || mat.polygonOffsetUnits < 0)) {
            flags |= InstanceFlag.DepthOffset;
        } else if (mat?.polygonOffset && (mat.polygonOffsetFactor > 0 || mat.polygonOffsetUnits > 0)) {
            flags |= InstanceFlag.DepthBack;
        }
        // A grid lies under the model: three.js draws it before the meshes, so a face on its plane
        // hides it. Edges of a model, drawn after its faces, still win.
        if ((obj as THREE.GridHelper).type === "GridHelper") flags |= InstanceFlag.Underlay;
        // Like three.js' GpuMeshPicker, only model meshes are pickable: edges, grid, overlays and
        // helpers must never stand in front of the object a click is meant for.
        const pickablePoints = this.pickPoints && e.topology === Topology.Points && !!geometry.attributes.pickColor;
        if (!(obj instanceof CustomBatchedMesh) && !pickablePoints) flags |= InstanceFlag.NoPick;
        const influence = (obj as THREE.Mesh).morphTargetInfluences?.[0] ?? 0;
        // MeshStandardMaterial's metalness/roughness; Lambert/Phong light as diffuse-only.
        const pbr = mat as unknown as {isMeshStandardMaterial?: boolean; metalness?: number; roughness?: number};
        const specular = !!pbr?.isMeshStandardMaterial;
        const metalness = specular ? (pbr.metalness ?? 0) : 0;
        const roughness = specular ? (pbr.roughness ?? 1) : 1;
        const lineWidth = this.lineWidth(obj, mat);
        const [dash, gap] = dashOf(mat);

        const heap = this.module.HEAPF32;
        const inst = obj as THREE.InstancedMesh;
        for (let i = 0; i < e.instances.length; i++) {
            if (inst.isInstancedMesh) {
                inst.getMatrixAt(i, this.tmpMatrix);
                this.tmpMatrix.premultiply(obj.matrixWorld);
                heap.set(this.tmpMatrix.elements, this.matrixPtr >> 2);
            } else {
                heap.set(obj.matrixWorld.elements, this.matrixPtr >> 2);
            }
            this.r.setInstance(
                e.instances[i], this.matrixPtr, color.r, color.g, color.b, opacity, flags, influence,
                metalness, roughness, specular, lineWidth, dash, gap,
            );
        }
    }

    /** Device-pixel width of a fat line (LineMaterial.linewidth, in CSS px unless in world units) or
     *  diameter of a point (PointsMaterial.size without size attenuation); 1 for everything else. */
    private lineWidth(obj: THREE.Object3D, mat: AnyMaterial | undefined): number {
        const o = obj as unknown as Record<string, boolean>;
        const m = mat as unknown as {linewidth?: number; worldUnits?: boolean; size?: number; sizeAttenuation?: boolean} | undefined;
        if ((o.isLineSegments2 || o.isLine2) && m?.linewidth && !m.worldUnits) return m.linewidth * this.pixelRatio;
        if (o.isPoints && m?.size && m.sizeAttenuation === false) return m.size * this.pixelRatio;
        return 1;
    }

    private pushSegmentStates(obj: THREE.Object3D, e: Entry): void {
        let packed: Uint32Array | null = null;
        if (e.segmentKind === "batched" && obj instanceof CustomBatchedMesh) {
            if (obj.renderStateVersion === e.stateVersion) return;
            e.stateVersion = obj.renderStateVersion;
            const ids = e.segmentIds!;
            const highlight = packColor(selectedMaterial.color);
            packed = new Uint32Array(ids.length * 2);
            for (let s = 0; s < ids.length; s++) {
                const id = ids[s];
                if (!id) continue;
                if (obj.isRangeHidden(id)) packed[s * 2 + 1] = SegmentFlag.Hidden;
                else if (obj.isRangeSelected(id)) {
                    // Lit as three.js lights selectedMaterial (matte), not the model's own material: on a
                    // metallic glTF material the highlight would come out almost black.
                    packed[s * 2] = highlight;
                    packed[s * 2 + 1] = SegmentFlag.Matte;
                }
            }
        } else if (e.segmentKind === "edges") {
            const tex = baseMaterial(obj)?.uniforms?.uVisibleTex?.value as THREE.DataTexture | undefined;
            if (!tex || tex.version === e.stateVersion) return;
            e.stateVersion = tex.version;
            const vis = tex.image.data as Uint8Array;
            const idx = e.segmentRangeIdx!;
            packed = new Uint32Array(idx.length * 2);
            for (let s = 0; s < idx.length; s++) {
                if (vis[idx[s]] === 0) packed[s * 2 + 1] = SegmentFlag.Hidden;
            }
        } else if (e.segmentKind === "groups") {
            const mats = (obj as THREE.Mesh).material as AnyMaterial[];
            const groups = ((obj as THREE.Mesh).geometry as THREE.BufferGeometry).groups;
            packed = new Uint32Array(groups.length * 2);
            for (let s = 0; s < groups.length; s++) {
                const m = mats[groups[s].materialIndex ?? 0];
                if (!m || !m.visible) packed[s * 2 + 1] = SegmentFlag.Hidden;
                else if (m.color instanceof THREE.Color) packed[s * 2] = packColor(m.color);
            }
        }
        if (!packed) return;
        const p = this.module._malloc(packed.byteLength);
        this.module.HEAPU32.set(packed, p >> 2);
        try {
            for (const id of e.instances) this.r.setSegmentStates(id, p, packed.length / 2);
        } finally {
            this.module._free(p);
        }
    }

    private withF32(a: Float32Array, fn: (ptr: number, count: number) => void): void {
        const p = this.module._malloc(a.byteLength);
        this.module.HEAPF32.set(a, p >> 2);
        try {
            fn(p, a.length);
        } finally {
            this.module._free(p);
        }
    }
}
