// Images lying in the world (viewport/api/imageLayer.ts) on the three.js-free viewport: each one a
// textured quad of the renderer. The pixels are a texture without a mip chain, written in place when
// they are replaced; a pixel's own alpha is drawn, and the texture is sampled blended or, for a grid
// of cells, without blending (`nearest`).
//
// The renderer's quad is the unit square, and a texture's first row lies on its upper edge. An
// image's first row lies at its origin and its rows follow along `v`, so the quad's own y axis is
// placed against `v`: the rows are where the convention has them without a buffer being reversed.
//
// A load that empties the renderer's scene takes quads and textures with it; the layer keeps each
// image's pixels and makes them again, as the overlays do.
import {InstanceFlag} from "@/viewport/adacpp/types";
import {validImage, type ImageLayer, type ImageRect, type WorldImage} from "@/viewport/api/imageLayer";
import type {NativeScene} from "./nativeScene";

/** What a module without quads answers. */
const NO_QUADS = "The WebGPU renderer module is older than this viewer and draws no images in the scene.";

interface Item {
    rect: ImageRect;
    width: number;
    height: number;
    /** The pixels as given last (a copy): what the texture is made from again. */
    pixels: Uint8Array;
    opacity: number;
    visible: boolean;
    onTop: boolean;
    clipped: boolean;
    nearest: boolean;
    matrix: number[] | null;
    /** In the renderer, while it is drawn. */
    quad: number | null;
    texture: number | null;
}

const LOCAL = new Float64Array(16);

/** The matrix (column-major 4x4) that takes the renderer's unit square to an image's rectangle,
 *  into `out`: x along `u`, y against `v` (the texture's first row at the origin), the middle of the
 *  square at the middle of the rectangle; then `placed`, where the image's coordinates stand in the
 *  world, when there is one. Allocates nothing. */
export function quadMatrix<T extends Float32Array | number[]>(rect: ImageRect, placed: ArrayLike<number> | null, out: T): T {
    const {origin: o, u, v} = rect;
    // The third axis only has to be off the rectangle's plane: its normal.
    const nx = u[1] * v[2] - u[2] * v[1], ny = u[2] * v[0] - u[0] * v[2], nz = u[0] * v[1] - u[1] * v[0];
    const length = Math.hypot(nx, ny, nz) || 1;
    const local = LOCAL;
    for (let k = 0; k < 3; k++) {
        local[k] = u[k];
        local[4 + k] = -v[k];
        local[12 + k] = o[k] + (u[k] + v[k]) / 2;
    }
    local[8] = nx / length;
    local[9] = ny / length;
    local[10] = nz / length;
    local[3] = local[7] = local[11] = 0;
    local[15] = 1;
    if (!placed) {
        for (let i = 0; i < 16; i++) out[i] = local[i];
        return out;
    }
    for (let c = 0; c < 4; c++) {
        for (let r = 0; r < 4; r++) {
            out[c * 4 + r] = placed[r] * local[c * 4] + placed[4 + r] * local[c * 4 + 1] + placed[8 + r] * local[c * 4 + 2] + placed[12 + r] * local[c * 4 + 3];
        }
    }
    return out;
}

export function createNativeImageLayer(scene: NativeScene): ImageLayer {
    const items = new Map<number, Item>();
    let nextHandle = 1;
    let disposed = false;
    const matrix = new Float32Array(16);

    /** The quad's place and look, from the item. */
    const place = (item: Item): void => {
        if (item.quad === null || item.texture === null) return;
        const flags =
            (item.visible ? 0 : InstanceFlag.Hidden) |
            (item.clipped ? InstanceFlag.Clipped : 0) |
            // In front of a floor it lies on, or over everything.
            (item.onTop ? InstanceFlag.OnTop : InstanceFlag.DepthOffset) |
            (item.nearest ? InstanceFlag.Nearest : 0);
        scene.setQuad(item.quad, item.texture, quadMatrix(item.rect, item.matrix, matrix), item.opacity, flags);
    };

    const draw = (item: Item): void => {
        item.quad = item.texture = null;
        if (!scene.drawsQuads || !validImage(item.pixels, item.width, item.height)) return;
        try {
            item.texture = scene.addPlainTexture(item.pixels, item.width, item.height);
            item.quad = scene.addQuad();
            place(item);
        } catch (err) {
            // An image that cannot be drawn must not take the feature that asked for it down.
            console.warn("[adacpp-native] an image could not be drawn:", err);
            item.quad = item.texture = null;
        }
    };

    const release = (item: Item): void => {
        try {
            if (item.quad !== null) scene.removeQuad(item.quad);
            if (item.texture !== null) scene.removeTexture(item.texture);
        } catch (err) {
            // The renderer may already have stopped (a lost device); there is nothing left to free.
            console.warn("[adacpp-native] releasing an image:", err);
        }
        item.quad = item.texture = null;
    };

    // A load that empties the renderer's scene takes what was drawn here with it: make it again.
    let epoch = scene.sceneEpoch;
    const afterLoad = (): void => {
        if (disposed || epoch === scene.sceneEpoch) return;
        epoch = scene.sceneEpoch;
        for (const item of items.values()) draw(item);
    };
    scene.onLoaded.add(afterLoad);

    const drop = (handle: number): void => {
        const item = items.get(handle);
        if (!item) return;
        items.delete(handle);
        release(item);
    };

    const keep = (pixels: ArrayLike<number>, width: number, height: number, into: Uint8Array | null): Uint8Array => {
        const size = width * height * 4;
        const out = into && into.length === size ? into : new Uint8Array(size);
        if (pixels.length === size) out.set(pixels);
        else for (let i = 0; i < size; i++) out[i] = pixels[i];
        return out;
    };

    return {
        add: (image: WorldImage) => {
            const ok = validImage(image.pixels, image.width, image.height);
            const item: Item = {
                rect: {origin: [...image.origin], u: [...image.u], v: [...image.v]},
                width: ok ? image.width : 0,
                height: ok ? image.height : 0,
                pixels: ok ? keep(image.pixels, image.width, image.height, null) : new Uint8Array(0),
                opacity: Math.max(0, Math.min(1, image.opacity ?? 1)),
                visible: image.visible !== false,
                onTop: !!image.onTop,
                clipped: !!image.clipped,
                nearest: !!image.nearest,
                matrix: image.matrix ? Array.from(image.matrix) : null,
                quad: null,
                texture: null,
            };
            const handle = nextHandle++;
            items.set(handle, item);
            draw(item);
            return handle;
        },
        setPixels: (handle, pixels, width, height) => {
            const item = items.get(handle);
            if (!item) return;
            const w = width ?? item.width, h = height ?? item.height;
            if (!validImage(pixels, w, h)) return;
            item.pixels = keep(pixels, w, h, item.pixels);
            item.width = w;
            item.height = h;
            // The texture keeps its id at another size too: the quad is as it was.
            if (item.texture !== null) scene.updateTexture(item.texture, item.pixels, w, h);
            else draw(item);
        },
        setRect: (handle, rect) => {
            const item = items.get(handle);
            if (!item) return;
            item.rect = {origin: [...rect.origin], u: [...rect.u], v: [...rect.v]};
            place(item);
        },
        setLook: (handle, look) => {
            const item = items.get(handle);
            if (!item) return;
            if (look.visible !== undefined) item.visible = look.visible;
            if (look.opacity !== undefined) item.opacity = Math.max(0, Math.min(1, look.opacity));
            if (look.matrix !== undefined) item.matrix = look.matrix ? Array.from(look.matrix) : null;
            place(item);
        },
        // A texture as large as the GPU takes.
        unavailable: () => (scene.drawsQuads ? null : NO_QUADS),
        remove: drop,
        clear: () => {
            for (const handle of [...items.keys()]) drop(handle);
        },
        dispose: () => {
            for (const handle of [...items.keys()]) drop(handle);
            disposed = true;
            scene.onLoaded.delete(afterLoad);
        },
    };
}
