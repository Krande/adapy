// Images lying in the world (viewport/api/imageLayer.ts) on the three.js-free viewport.
//
// The renderer textures sprites, which face the camera, and not geometry in the world. Until it
// does, an image is drawn as a mesh of one flat square per pixel, coloured through the vertex
// colours (`cellGridDrawer` below). That is exact for a grid of cells and what `nearest` asks for;
// it costs four vertices per pixel, so images above `MAX_CELLS` pixels are not drawn and the
// viewport says so (`unavailable`). What the interim cannot do:
//   - blend between pixels: an image is always drawn as flat cells, `nearest` or not;
//   - a pixel's own transparency: the renderer takes one alpha for a whole instance, so a pixel
//     is drawn (alpha of a half or more) or left out, and the image's opacity applies to all of it.
//
// How an image reaches the renderer is one small interface, `ImageDrawer`. A textured quad
// (`addTexture` / `updateTexture` on world geometry) is another implementation of it, chosen in
// `drawerFor`; nothing else here changes. One thing for that implementation: an image's row 0 lies
// at its origin (viewport/api/imageLayer.ts), while the renderer's textures are top row first and a
// textured quad has the top row on its +y edge, so the quad's texture coordinates run against `v`
// (or the quad is given the rectangle from `origin + v` with `-v`). The rows are not reversed.
import {validImage, type ImageLayer, type ImageRect, type WorldImage} from "@/viewport/api/imageLayer";
import type {NativeResultLayer} from "./nativeResultLayer";
import type {NativeScene} from "./nativeScene";

/** The most pixels an image is drawn with as a grid of cells: 512 x 512. Measured in Chromium on
 *  a software adapter, replacing the pixels takes about 3 ms for a 384 x 384 image and about 8 ms
 *  for a 512 x 512 one, and making the mesh (once per image and size) 0.3 s and 0.5 s. A vertex is
 *  28 bytes on the GPU, and 16 of them are kept on the module heap: 29 MB and 17 MB at the limit. */
export const MAX_CELLS = 512 * 512;

/** What an image is drawn from: kept so it can be made again after a load emptied the scene. */
interface Held {
    rect: ImageRect;
    width: number;
    height: number;
    /** The pixels as given last (a copy). */
    pixels: Uint8Array;
    opacity: number;
    visible: boolean;
    onTop: boolean;
    clipped: boolean;
    matrix: number[] | null;
}

/** One image in the renderer. */
interface ImageDrawer {
    /** New pixels of the size it was made with. */
    paint(pixels: Uint8Array): void;
    setLook(visible: boolean, opacity: number): void;
    setMatrix(matrix: number[] | null): void;
    remove(): void;
}

/** sRGB bytes to the linear bytes the renderer keeps vertex colours in. */
const LINEAR = (() => {
    const table = new Uint8Array(256);
    for (let i = 0; i < 256; i++) {
        const c = i / 255;
        table[i] = Math.round(255 * (c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4)));
    }
    return table;
})();

/** A pixel with less alpha than this is left out. */
const DRAWN_ALPHA = 128;

/** The image as a mesh of one square per pixel: four vertices each, so every pixel has its own
 *  colour to its edge. A pixel that is not drawn has its square folded onto one corner through the
 *  mesh's morph, which is written again only when the set of drawn pixels changed. */
function cellGridDrawer(scene: NativeScene, held: Held): ImageDrawer {
    const {width: w, height: h} = held;
    const cells = w * h;
    const {origin: o, u, v} = held.rect;
    const positions = new Float32Array(cells * 12);
    const indices = new Uint32Array(cells * 6);
    // One pixel's step along the rows and across them.
    const du = [u[0] / w, u[1] / w, u[2] / w];
    const dv = [v[0] / h, v[1] / h, v[2] / h];
    for (let j = 0, c = 0; j < h; j++) {
        for (let i = 0; i < w; i++, c++) {
            const p = c * 12;
            for (let k = 0; k < 3; k++) {
                const at = o[k] + i * du[k] + j * dv[k];
                positions[p + k] = at;
                positions[p + 3 + k] = at + du[k];
                positions[p + 6 + k] = at + du[k] + dv[k];
                positions[p + 9 + k] = at + dv[k];
            }
            const q = c * 4;
            const t = c * 6;
            indices[t] = indices[t + 3] = q;
            indices[t + 1] = q + 1;
            indices[t + 2] = indices[t + 4] = q + 2;
            indices[t + 5] = q + 3;
        }
    }
    const layer: NativeResultLayer = scene.createResultLayer({
        positions,
        indices,
        topology: "triangles",
        matrix: held.matrix,
        color: [1, 1, 1],
        opacity: held.opacity,
        overlay: {onTop: held.onTop, clipped: held.clipped},
    });
    layer.setInfluence(1);
    /** 1 for each pixel whose square is drawn. */
    const drawn = new Uint8Array(cells).fill(1);
    let pixels: Uint8Array = held.pixels;
    let folded = false;

    const colours = (heap: Uint32Array, at: number): void => {
        folded = false;
        for (let c = 0, s = 0, vtx = at; c < cells; c++, s += 4, vtx += 4) {
            const colour = (LINEAR[pixels[s]] | (LINEAR[pixels[s + 1]] << 8) | (LINEAR[pixels[s + 2]] << 16) | 0xff000000) >>> 0;
            heap[vtx] = heap[vtx + 1] = heap[vtx + 2] = heap[vtx + 3] = colour;
            const on = pixels[s + 3] >= DRAWN_ALPHA ? 1 : 0;
            if (on !== drawn[c]) folded = true;
        }
    };
    /** The squares of the pixels that came or went: unfolded, or folded onto their first corner. */
    const fold = (heap: Float32Array, at: number): void => {
        for (let c = 0, s = 3; c < cells; c++, s += 4) {
            const on = pixels[s] >= DRAWN_ALPHA ? 1 : 0;
            if (on === drawn[c]) continue;
            drawn[c] = on;
            const m = at + c * 12;
            const k = on ? 0 : -1;
            for (let a = 0; a < 3; a++) {
                heap[m + 3 + a] = k * du[a];
                heap[m + 6 + a] = k * (du[a] + dv[a]);
                heap[m + 9 + a] = k * dv[a];
            }
        }
    };
    const paint = (next: Uint8Array): void => {
        pixels = next;
        layer.writePackedColors(colours);
        if (folded) layer.writeDisplacement(fold);
    };
    paint(held.pixels);
    if (!held.visible) layer.setVisible(false);

    return {
        paint,
        setLook: (visible, opacity) => {
            layer.setVisible(visible);
            layer.setOpacity(opacity);
        },
        setMatrix: (matrix) => layer.setMatrix(matrix),
        remove: () => layer.remove(),
    };
}

/** Why an image of this size is not drawn; null when it is. */
function tooLarge(width: number, height: number): string | null {
    if (width * height <= MAX_CELLS) return null;
    return `This renderer draws an image in the scene one square per pixel, up to ${MAX_CELLS.toLocaleString("en")} pixels; this one has ${(width * height).toLocaleString("en")} (${width} x ${height}).`;
}

/** The way this renderer draws an image; null when it cannot draw this one. The place a textured
 *  quad is chosen instead, once the renderer has one. */
function drawerFor(scene: NativeScene, held: Held): ImageDrawer | null {
    if (tooLarge(held.width, held.height)) return null;
    return cellGridDrawer(scene, held);
}

interface Item {
    held: Held;
    drawer: ImageDrawer | null;
}

export function createNativeImageLayer(scene: NativeScene): ImageLayer {
    const items = new Map<number, Item>();
    let nextHandle = 1;
    let disposed = false;
    let warned = false;

    const draw = (item: Item): void => {
        item.drawer = null;
        const {held} = item;
        if (!validImage(held.pixels, held.width, held.height)) return;
        const reason = tooLarge(held.width, held.height);
        if (reason) {
            if (!warned) console.warn(`[adacpp-native] ${reason}`);
            warned = true;
            return;
        }
        try {
            item.drawer = drawerFor(scene, held);
        } catch (err) {
            // An image that cannot be drawn must not take the feature that asked for it down.
            console.warn("[adacpp-native] an image could not be drawn:", err);
        }
    };

    const release = (item: Item): void => {
        try {
            item.drawer?.remove();
        } catch (err) {
            // The renderer may already have stopped (a lost device); there is nothing left to free.
            console.warn("[adacpp-native] releasing an image:", err);
        }
        item.drawer = null;
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
                held: {
                    rect: {origin: [...image.origin], u: [...image.u], v: [...image.v]},
                    width: ok ? image.width : 0,
                    height: ok ? image.height : 0,
                    pixels: ok ? keep(image.pixels, image.width, image.height, null) : new Uint8Array(0),
                    opacity: Math.max(0, Math.min(1, image.opacity ?? 1)),
                    visible: image.visible !== false,
                    onTop: !!image.onTop,
                    clipped: !!image.clipped,
                    matrix: image.matrix ? Array.from(image.matrix) : null,
                },
                drawer: null,
            };
            const handle = nextHandle++;
            items.set(handle, item);
            draw(item);
            return handle;
        },
        setPixels: (handle, pixels, width, height) => {
            const item = items.get(handle);
            if (!item) return;
            const {held} = item;
            const w = width ?? held.width, h = height ?? held.height;
            if (!validImage(pixels, w, h)) return;
            const same = w === held.width && h === held.height;
            held.pixels = keep(pixels, w, h, held.pixels);
            if (same && item.drawer) return item.drawer.paint(held.pixels);
            // Another size is another mesh.
            held.width = w;
            held.height = h;
            release(item);
            draw(item);
        },
        setRect: (handle, rect) => {
            const item = items.get(handle);
            if (!item) return;
            item.held.rect = {origin: [...rect.origin], u: [...rect.u], v: [...rect.v]};
            release(item);
            draw(item);
        },
        setLook: (handle, look) => {
            const item = items.get(handle);
            if (!item) return;
            const {held} = item;
            if (look.visible !== undefined) held.visible = look.visible;
            if (look.opacity !== undefined) held.opacity = Math.max(0, Math.min(1, look.opacity));
            if (look.visible !== undefined || look.opacity !== undefined) item.drawer?.setLook(held.visible, held.opacity);
            if (look.matrix !== undefined) {
                held.matrix = look.matrix ? Array.from(look.matrix) : null;
                item.drawer?.setMatrix(held.matrix);
            }
        },
        unavailable: tooLarge,
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
