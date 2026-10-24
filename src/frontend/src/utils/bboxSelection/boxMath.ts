/**
 * Pure box math for the selection box -- no three.js, node-testable.
 *
 * A selection box is an axis-aligned world-space box {min, max}. An element is
 * "fully within" when every vertex is inside it (equivalently: its AABB is), and
 * "partially within" when any of its triangles touches it.
 */

export type Vec3 = [number, number, number];

export interface Aabb {
    min: Vec3;
    max: Vec3;
}

export type BoxSelectMode = "partial" | "full";

/** The six faces in the order +X, -X, +Y, -Y, +Z, -Z. */
export const BOX_FACES: readonly {axis: 0 | 1 | 2; positive: boolean}[] = [
    {axis: 0, positive: true},
    {axis: 0, positive: false},
    {axis: 1, positive: true},
    {axis: 1, positive: false},
    {axis: 2, positive: true},
    {axis: 2, positive: false},
];

export function boxCenter(b: Aabb): Vec3 {
    return [(b.min[0] + b.max[0]) / 2, (b.min[1] + b.max[1]) / 2, (b.min[2] + b.max[2]) / 2];
}

export function boxSize(b: Aabb): Vec3 {
    return [b.max[0] - b.min[0], b.max[1] - b.min[1], b.max[2] - b.min[2]];
}

/** Centre of face `face` (index into BOX_FACES). */
export function faceCenter(b: Aabb, face: number): Vec3 {
    const c = boxCenter(b);
    const f = BOX_FACES[face];
    if (f) c[f.axis] = f.positive ? b.max[f.axis] : b.min[f.axis];
    return c;
}

/** Move one face by `offset` along its axis; the opposite face stays put and the
 *  box never gets thinner than `minSize`. */
export function moveFace(b: Aabb, face: number, offset: number, minSize: number): Aabb {
    const min: Vec3 = [...b.min];
    const max: Vec3 = [...b.max];
    const f = BOX_FACES[face];
    if (!f) return {min, max};
    if (f.positive) max[f.axis] = Math.max(min[f.axis] + minSize, max[f.axis] + offset);
    else min[f.axis] = Math.min(max[f.axis] - minSize, min[f.axis] + offset);
    return {min, max};
}

/** The box moved so its centre is `center`, size unchanged. */
export function centeredAt(b: Aabb, center: Vec3): Aabb {
    const s = boxSize(b);
    return {
        min: [center[0] - s[0] / 2, center[1] - s[1] / 2, center[2] - s[2] / 2],
        max: [center[0] + s[0] / 2, center[1] + s[1] / 2, center[2] + s[2] / 2],
    };
}

/** `b` grown by `pad` on every side. */
export function padded(b: Aabb, pad: number): Aabb {
    return {
        min: [b.min[0] - pad, b.min[1] - pad, b.min[2] - pad],
        max: [b.max[0] + pad, b.max[1] + pad, b.max[2] + pad],
    };
}

/** Do the boxes overlap (touching counts)? `a` is read from `bounds` at `i * 6`. */
export function overlapsAt(bounds: ArrayLike<number>, i: number, box: Aabb): boolean {
    const o = i * 6;
    return (
        bounds[o] <= box.max[0] && bounds[o + 3] >= box.min[0] &&
        bounds[o + 1] <= box.max[1] && bounds[o + 4] >= box.min[1] &&
        bounds[o + 2] <= box.max[2] && bounds[o + 5] >= box.min[2]
    );
}

/** Is the AABB at `i * 6` in `bounds` inside `box` (on the boundary counts)? */
export function containedAt(bounds: ArrayLike<number>, i: number, box: Aabb): boolean {
    const o = i * 6;
    return (
        bounds[o] >= box.min[0] && bounds[o + 3] <= box.max[0] &&
        bounds[o + 1] >= box.min[1] && bounds[o + 4] <= box.max[1] &&
        bounds[o + 2] >= box.min[2] && bounds[o + 5] <= box.max[2]
    );
}

/**
 * Does the triangle (a, b, c) touch the box? Separating-axis test: the box's
 * three axes, the triangle's normal and the nine edge cross products.
 */
export function triangleTouchesBox(a: Vec3, b: Vec3, c: Vec3, box: Aabb): boolean {
    const ctr = boxCenter(box);
    const h: Vec3 = [(box.max[0] - box.min[0]) / 2, (box.max[1] - box.min[1]) / 2, (box.max[2] - box.min[2]) / 2];
    const v0: Vec3 = [a[0] - ctr[0], a[1] - ctr[1], a[2] - ctr[2]];
    const v1: Vec3 = [b[0] - ctr[0], b[1] - ctr[1], b[2] - ctr[2]];
    const v2: Vec3 = [c[0] - ctr[0], c[1] - ctr[1], c[2] - ctr[2]];
    const e0: Vec3 = [v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2]];
    const e1: Vec3 = [v2[0] - v1[0], v2[1] - v1[1], v2[2] - v1[2]];
    const e2: Vec3 = [v0[0] - v2[0], v0[1] - v2[1], v0[2] - v2[2]];

    // Box axes.
    for (let k = 0; k < 3; k++) {
        const lo = Math.min(v0[k], v1[k], v2[k]);
        const hi = Math.max(v0[k], v1[k], v2[k]);
        if (lo > h[k] || hi < -h[k]) return false;
    }
    // Triangle normal.
    const n = cross(e0, e1);
    const r = h[0] * Math.abs(n[0]) + h[1] * Math.abs(n[1]) + h[2] * Math.abs(n[2]);
    if (Math.abs(dot(n, v0)) > r) return false;
    // Edge x box axis.
    for (const e of [e0, e1, e2]) {
        for (let k = 0; k < 3; k++) {
            const axis: Vec3 = [0, 0, 0];
            axis[k] = 1;
            const ax = cross(e, axis);
            const p0 = dot(ax, v0);
            const p1 = dot(ax, v1);
            const p2 = dot(ax, v2);
            const rad = h[0] * Math.abs(ax[0]) + h[1] * Math.abs(ax[1]) + h[2] * Math.abs(ax[2]);
            if (Math.min(p0, p1, p2) > rad || Math.max(p0, p1, p2) < -rad) return false;
        }
    }
    return true;
}

function cross(a: Vec3, b: Vec3): Vec3 {
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
}

function dot(a: Vec3, b: Vec3): number {
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
}
