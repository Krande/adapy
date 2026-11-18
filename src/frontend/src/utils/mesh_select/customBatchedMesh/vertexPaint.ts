// Writing per-vertex colours for the ranges of a `CustomBatchedMesh`: a range's colour from its
// packed value (0xff000000 | 0xRRGGBB in sRGB, or 0 for the mesh's own colour), and painting it
// into the colour attribute through the index. Plain functions over arrays.

/** sRGB byte -> linear channel, the conversion three.js applies to an sRGB colour. */
export const SRGB_TO_LINEAR: Float32Array = new Float32Array(256).map((_, i) => {
    const c = i / 255;
    return c < 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
});

/** The linear RGB a range is painted in: `value`'s channels, or `own` when `value` is 0. */
export function rangeRgb(value: number, own: {r: number; g: number; b: number}): [number, number, number] {
    if (!value) return [own.r, own.g, own.b];
    return [SRGB_TO_LINEAR[(value >> 16) & 255], SRGB_TO_LINEAR[(value >> 8) & 255], SRGB_TO_LINEAR[value & 255]];
}

/** Paint the vertices of index entries `start .. start + count` in `rgb`. */
export function paintIndexedRange(
    arr: Float32Array,
    index: ArrayLike<number>,
    start: number,
    count: number,
    [r, g, b]: readonly [number, number, number],
): void {
    for (let j = start, end = start + count; j < end; j++) {
        const v = index[j] * 3;
        arr[v] = r;
        arr[v + 1] = g;
        arr[v + 2] = b;
    }
}

/** Every vertex in one colour. */
export function fillVertexColors(arr: Float32Array, {r, g, b}: {r: number; g: number; b: number}): void {
    for (let i = 0; i < arr.length; i += 3) {
        arr[i] = r;
        arr[i + 1] = g;
        arr[i + 2] = b;
    }
}
