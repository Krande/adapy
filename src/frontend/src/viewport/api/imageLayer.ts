// Images lying in the world: a rectangle textured with pixels that are replaced in place. What a map
// under a moving model is drawn with (an occupancy grid, a cost map, a heat map), one pixel per
// cell. A feature hands over plain data (where the rectangle is, RGBA bytes) and the viewport on
// screen draws it: three.js as a textured mesh, the three.js-free viewport with what its renderer
// has. Beside the overlay layer (./overlayLayer.ts, lines and points) and the billboards
// (./billboards.ts, images that face the camera). Nothing here (or anything it imports at runtime)
// may import three.js.
import type {Point3} from "./billboards";

/** Where an image lies: a parallelogram from `origin` along `u` and along `v` (a rectangle when
 *  they are at right angles). The world is z-up, so a map on the ground has u and v in the xy plane. */
export interface ImageRect {
    origin: Point3;
    /** From the origin along the image's rows: the whole width. */
    u: Point3;
    /** From the origin across the rows: the whole height. */
    v: Point3;
}

export interface WorldImage extends ImageRect {
    /** Pixels per row, and rows. */
    width: number;
    height: number;
    /** RGBA, four bytes per pixel, sRGB with straight alpha. Row 0 lies on the edge at the origin
     *  that runs along `u`, column 0 on the edge at the origin that runs along `v`: pixel (column i,
     *  row j) covers `origin + (i..i+1)/width * u + (j..j+1)/height * v`. So with the origin at the
     *  rectangle's least x and y, `u` along +x and `v` along +y, row 0 is the min-y edge and column 0
     *  the min-x edge, as a grid of cells is stored. An image stored top row first is given with the
     *  origin at its top-left corner and `v` pointing down: the rectangle says how the rows lie, and
     *  no buffer is flipped. Read during the call and not kept. */
    pixels: Uint8Array | Uint8ClampedArray;
    /** How opaque the whole image is, 0..1, times each pixel's own alpha; 1 when left out. */
    opacity?: number;
    /** False adds it hidden. */
    visible?: boolean;
    /** Drawn over the model instead of hidden behind what is in front of it. */
    onTop?: boolean;
    /** Each pixel a flat square (nearest-neighbour sampling): a grid of cells must not blur.
     *  Left out, pixels are blended into their neighbours. */
    nearest?: boolean;
    /** Cut by the section planes, as the model is. */
    clipped?: boolean;
    /** Where the coordinates stand in the world: a column-major 4x4, identity when absent. */
    matrix?: ArrayLike<number> | null;
}

/** What of an image's look can change after it was added. A member left out stays as it is. */
export interface ImageLookChange {
    visible?: boolean;
    opacity?: number;
    matrix?: ArrayLike<number> | null;
}

/** One feature's images in a viewport. They take no part in picking the model's objects. */
export interface ImageLayer {
    /** Returns the handle the other calls take. An image the viewport cannot draw (see
     *  `unavailable`) still gets a handle and draws nothing until it is given pixels it can. */
    add(image: WorldImage): number;
    /** Replace the pixels. With the size they had, what the viewport holds is written in place;
     *  `width` and `height` give another size. */
    setPixels(handle: number, pixels: Uint8Array | Uint8ClampedArray, width?: number, height?: number): void;
    /** Move the image. */
    setRect(handle: number, rect: ImageRect): void;
    setLook(handle: number, look: ImageLookChange): void;
    /** Why this viewport cannot draw an image of `width` x `height` pixels, in words for the user;
     *  null when it can. */
    unavailable(width: number, height: number): string | null;
    remove(handle: number): void;
    clear(): void;
    /** Remove everything and release what the layer holds; the layer is not used again. */
    dispose(): void;
}

/** A layer that draws nothing: what a caller without a viewport gets. */
export const NO_IMAGES: ImageLayer = {
    add: () => 0,
    setPixels: () => {},
    setRect: () => {},
    setLook: () => {},
    unavailable: () => null,
    remove: () => {},
    clear: () => {},
    dispose: () => {},
};

/** The four corners of an image's rectangle (origin, +u, +u+v, +v), xyz each, into `out`. */
export function rectCorners(rect: ImageRect, out: Float32Array): Float32Array {
    const {origin: o, u, v} = rect;
    for (let k = 0; k < 3; k++) {
        out[k] = o[k];
        out[3 + k] = o[k] + u[k];
        out[6 + k] = o[k] + u[k] + v[k];
        out[9 + k] = o[k] + v[k];
    }
    return out;
}

/** Whether `pixels` holds an image of `width` x `height` RGBA pixels. */
export function validImage(pixels: ArrayLike<number>, width: number, height: number): boolean {
    return Number.isInteger(width) && Number.isInteger(height) && width > 0 && height > 0 && pixels.length >= width * height * 4;
}
