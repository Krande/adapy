// 4 x 4 matrices as sixteen numbers, column-major (the glTF and WebGPU layout): what the viewports'
// own adapters compute with where there is no scene library to do it. No three.js in this module.

export type Mat4 = Float64Array | Float32Array | number[];

export function identity4(out: Mat4 = new Float64Array(16)): Mat4 {
    for (let i = 0; i < 16; i++) out[i] = i % 5 === 0 ? 1 : 0;
    return out;
}

/** out = a * b. `out` may be `a` or `b`. `aAt` and `bAt` are where each starts in its array. */
export function multiply4(out: Mat4, a: ArrayLike<number>, b: ArrayLike<number>, aAt = 0, bAt = 0): Mat4 {
    const r = new Array<number>(16);
    for (let col = 0; col < 4; col++) {
        const b0 = b[bAt + col * 4], b1 = b[bAt + col * 4 + 1], b2 = b[bAt + col * 4 + 2], b3 = b[bAt + col * 4 + 3];
        for (let row = 0; row < 4; row++) {
            r[col * 4 + row] = a[aAt + row] * b0 + a[aAt + 4 + row] * b1 + a[aAt + 8 + row] * b2 + a[aAt + 12 + row] * b3;
        }
    }
    for (let i = 0; i < 16; i++) out[i] = r[i];
    return out;
}

/** out = the inverse of `m`. False, with `out` untouched, when `m` has none. `out` may be `m`. */
export function invert4(out: Mat4, m: ArrayLike<number>, at = 0): boolean {
    const a00 = m[at], a01 = m[at + 1], a02 = m[at + 2], a03 = m[at + 3];
    const a10 = m[at + 4], a11 = m[at + 5], a12 = m[at + 6], a13 = m[at + 7];
    const a20 = m[at + 8], a21 = m[at + 9], a22 = m[at + 10], a23 = m[at + 11];
    const a30 = m[at + 12], a31 = m[at + 13], a32 = m[at + 14], a33 = m[at + 15];
    const b00 = a00 * a11 - a01 * a10, b01 = a00 * a12 - a02 * a10, b02 = a00 * a13 - a03 * a10;
    const b03 = a01 * a12 - a02 * a11, b04 = a01 * a13 - a03 * a11, b05 = a02 * a13 - a03 * a12;
    const b06 = a20 * a31 - a21 * a30, b07 = a20 * a32 - a22 * a30, b08 = a20 * a33 - a23 * a30;
    const b09 = a21 * a32 - a22 * a31, b10 = a21 * a33 - a23 * a31, b11 = a22 * a33 - a23 * a32;
    const det = b00 * b11 - b01 * b10 + b02 * b09 + b03 * b08 - b04 * b07 + b05 * b06;
    if (!det || !Number.isFinite(det)) return false;
    const d = 1 / det;
    out[0] = (a11 * b11 - a12 * b10 + a13 * b09) * d;
    out[1] = (a02 * b10 - a01 * b11 - a03 * b09) * d;
    out[2] = (a31 * b05 - a32 * b04 + a33 * b03) * d;
    out[3] = (a22 * b04 - a21 * b05 - a23 * b03) * d;
    out[4] = (a12 * b08 - a10 * b11 - a13 * b07) * d;
    out[5] = (a00 * b11 - a02 * b08 + a03 * b07) * d;
    out[6] = (a32 * b02 - a30 * b05 - a33 * b01) * d;
    out[7] = (a20 * b05 - a22 * b02 + a23 * b01) * d;
    out[8] = (a10 * b10 - a11 * b08 + a13 * b06) * d;
    out[9] = (a01 * b08 - a00 * b10 - a03 * b06) * d;
    out[10] = (a30 * b04 - a31 * b02 + a33 * b00) * d;
    out[11] = (a21 * b02 - a20 * b04 - a23 * b00) * d;
    out[12] = (a11 * b07 - a10 * b09 - a12 * b06) * d;
    out[13] = (a00 * b09 - a01 * b07 + a02 * b06) * d;
    out[14] = (a31 * b01 - a30 * b03 - a32 * b00) * d;
    out[15] = (a20 * b03 - a21 * b01 + a22 * b00) * d;
    return true;
}

/** A glTF node's own transform: its `matrix`, or its translation, rotation (x, y, z, w) and scale. */
export function fromGltfNode(
    node: {matrix?: number[]; translation?: number[]; rotation?: number[]; scale?: number[]},
    out: Mat4 = new Float64Array(16),
): Mat4 {
    if (node.matrix?.length === 16) {
        for (let i = 0; i < 16; i++) out[i] = node.matrix[i];
        return out;
    }
    const [tx, ty, tz] = node.translation ?? [0, 0, 0];
    const [x, y, z, w] = node.rotation ?? [0, 0, 0, 1];
    const [sx, sy, sz] = node.scale ?? [1, 1, 1];
    out[0] = (1 - 2 * (y * y + z * z)) * sx;
    out[1] = 2 * (x * y + z * w) * sx;
    out[2] = 2 * (x * z - y * w) * sx;
    out[3] = 0;
    out[4] = 2 * (x * y - z * w) * sy;
    out[5] = (1 - 2 * (x * x + z * z)) * sy;
    out[6] = 2 * (y * z + x * w) * sy;
    out[7] = 0;
    out[8] = 2 * (x * z + y * w) * sz;
    out[9] = 2 * (y * z - x * w) * sz;
    out[10] = (1 - 2 * (x * x + y * y)) * sz;
    out[11] = 0;
    out[12] = tx;
    out[13] = ty;
    out[14] = tz;
    out[15] = 1;
    return out;
}
