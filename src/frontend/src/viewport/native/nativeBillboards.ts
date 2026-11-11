// Billboards (viewport/api/billboards.ts) on the three.js-free viewport: each one a sprite of the
// renderer, its image a texture made from a plain canvas' pixels.
import {SpriteFlag} from "@/viewport/adacpp/types";
import {billboardsAt, screenScale, type Billboard, type BillboardCamera, type BillboardLayer} from "@/viewport/api/billboards";
import {onModelLoaded} from "@/viewport/api/viewportApi";
import type {NativeScene} from "./nativeScene";

const DEFAULT_ORDER = 9995;

// three.js reads a texture that names no colour space as linear and encodes the result for the screen;
// the renderer takes sRGB pixels. Encoding such an image's pixels here gives the picture three.js gives.
const LINEAR_TO_SRGB = Uint8Array.from({length: 256}, (_, i) => {
    const c = i / 255;
    return Math.round(255 * (c <= 0.0031308 ? c * 12.92 : 1.055 * Math.pow(c, 1 / 2.4) - 0.055));
});

/** One image's pixels, kept so the texture can be made again after a load, and its texture. */
interface SharedImage {
    pixels: Uint8ClampedArray;
    width: number;
    height: number;
    texture: number | null;
    uses: number;
}

interface Item {
    billboard: Billboard;
    sprite: number | null;
}

function pixelsOf(billboard: Billboard): {pixels: Uint8ClampedArray; width: number; height: number} | null {
    const canvas = billboard.image.draw();
    const data = canvas.width && canvas.height ? canvas.getContext("2d")?.getImageData(0, 0, canvas.width, canvas.height) : null;
    if (!data) return null;
    const pixels = data.data;
    if (billboard.image.untagged) {
        for (let i = 0; i < pixels.length; i += 4) {
            pixels[i] = LINEAR_TO_SRGB[pixels[i]];
            pixels[i + 1] = LINEAR_TO_SRGB[pixels[i + 1]];
            pixels[i + 2] = LINEAR_TO_SRGB[pixels[i + 2]];
        }
    }
    return {pixels, width: data.width, height: data.height};
}

export function createNativeBillboardLayer(scene: NativeScene): BillboardLayer {
    const images = new Map<string, SharedImage>();
    const items = new Map<number, Item>();
    let nextHandle = 1;
    let epoch = scene.sceneEpoch;
    let disposed = false;
    const supported = scene.drawsSprites;
    if (!supported) console.warn("[adacpp-native] the WebGPU renderer module draws no sprites: labels and icons are not shown");

    const acquire = (b: Billboard): SharedImage | null => {
        let shared = images.get(b.image.key);
        if (!shared) {
            const drawn = pixelsOf(b);
            if (!drawn) return null;
            shared = {...drawn, texture: null, uses: 0};
            images.set(b.image.key, shared);
        }
        shared.uses++;
        return shared;
    };
    const release = (key: string): void => {
        const shared = images.get(key);
        if (!shared || --shared.uses > 0) return;
        if (shared.texture !== null) scene.removeTexture(shared.texture);
        images.delete(key);
    };

    /** Write one billboard to the renderer, making its sprite and its image's texture when missing. */
    const draw = (item: Item): void => {
        const b = item.billboard;
        const shared = images.get(b.image.key);
        if (!supported || !shared) return;
        try {
            shared.texture ??= scene.addTexture(shared.pixels, shared.width, shared.height);
            item.sprite ??= scene.addSprite();
            const aspect = shared.width / shared.height;
            const height =
                b.sizing === "world" ? b.height : screenScale(b.height, scene.elements.view.clientHeight, scene.cameraPose.fov);
            scene.setSprite(
                item.sprite, shared.texture, b.position, height * aspect, height, b.anchor?.[0] ?? 0.5, b.anchor?.[1] ?? 0.5,
                (b.sizing === "world" ? SpriteFlag.SizeAttenuation : 0) | (b.clipped ? SpriteFlag.Clipped : 0), b.order ?? DEFAULT_ORDER,
            );
        } catch (err) {
            // A label that cannot be drawn must not take the feature that asked for it down.
            console.warn("[adacpp-native] a billboard could not be drawn:", err);
        }
    };

    /** A load emptied the renderer's scene: the sprites and textures held here are gone with it, and
     *  their numbers may since stand for others. Forget them and make them again. */
    const afterLoad = (): void => {
        if (disposed || epoch === scene.sceneEpoch) return;
        epoch = scene.sceneEpoch;
        for (const shared of images.values()) shared.texture = null;
        for (const item of items.values()) item.sprite = null;
        for (const item of items.values()) draw(item);
    };
    const offLoaded = onModelLoaded(afterLoad);

    // A billboard of constant size on screen is scaled by the view's height.
    const resize = new ResizeObserver(() => {
        for (const item of items.values()) if (item.billboard.sizing === "screen") draw(item);
    });
    resize.observe(scene.elements.view);

    const drop = (handle: number): void => {
        const item = items.get(handle);
        if (!item) return;
        if (item.sprite !== null) scene.removeSprite(item.sprite);
        release(item.billboard.image.key);
        items.delete(handle);
    };

    return {
        add: (b) => {
            afterLoad();
            const handle = nextHandle++;
            if (acquire(b)) {
                const item: Item = {billboard: b, sprite: null};
                items.set(handle, item);
                draw(item);
            }
            return handle;
        },
        update: (handle, b) => {
            afterLoad();
            const item = items.get(handle);
            if (!item) return;
            // The new image is held before the old one is let go, so an unchanged image keeps its texture.
            if (!acquire(b)) return;
            release(item.billboard.image.key);
            item.billboard = b;
            draw(item);
        },
        remove: (handle) => {
            afterLoad();
            drop(handle);
        },
        clear: () => {
            afterLoad();
            for (const handle of [...items.keys()]) drop(handle);
        },
        onClick: (listener) => {
            const {view, input} = scene.elements;
            const onClick = (ev: MouseEvent) => {
                const rect = view.getBoundingClientRect();
                if (!items.size || rect.width <= 0 || rect.height <= 0) return;
                const pose = scene.cameraPose;
                if (pose.orthographic) return;
                const camera: BillboardCamera = {eye: pose.eye, target: pose.target, up: pose.up, fovDeg: pose.fov};
                const placed = [...items.values()].map(({billboard: b}) => {
                    const shared = images.get(b.image.key);
                    return {...b, aspect: shared ? shared.width / shared.height : 1};
                });
                const tags = billboardsAt(camera, rect.width, rect.height, placed, ev.clientX - rect.left, ev.clientY - rect.top);
                if (tags.length) listener(tags, ev);
            };
            input.addEventListener("click", onClick);
            return () => input.removeEventListener("click", onClick);
        },
        dispose: () => {
            try {
                afterLoad();
                for (const handle of [...items.keys()]) drop(handle);
            } catch (err) {
                // The renderer may already have stopped (a lost device); there is nothing left to free.
                console.warn("[adacpp-native] releasing billboards:", err);
            }
            disposed = true;
            offLoaded();
            resize.disconnect();
        },
    };
}
