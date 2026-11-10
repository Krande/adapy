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
    return triangleTouchesBoxAt(a[0], a[1], a[2], b[0], b[1], b[2], c[0], c[1], c[2], box);
}

/** `triangleTouchesBox` on plain numbers. It makes no array or object: a box dragged through a large
 *  model asks this for every triangle of every element on its boundary. */
export function triangleTouchesBoxAt(
    ax: number, ay: number, az: number,
    bx: number, by: number, bz: number,
    cx: number, cy: number, cz: number,
    box: Aabb,
): boolean {
    const mx = (box.min[0] + box.max[0]) / 2, my = (box.min[1] + box.max[1]) / 2, mz = (box.min[2] + box.max[2]) / 2;
    const hx = (box.max[0] - box.min[0]) / 2, hy = (box.max[1] - box.min[1]) / 2, hz = (box.max[2] - box.min[2]) / 2;
    // The triangle relative to the box's centre.
    const x0 = ax - mx, y0 = ay - my, z0 = az - mz;
    const x1 = bx - mx, y1 = by - my, z1 = bz - mz;
    const x2 = cx - mx, y2 = cy - my, z2 = cz - mz;

    // Box axes.
    if (Math.min(x0, x1, x2) > hx || Math.max(x0, x1, x2) < -hx) return false;
    if (Math.min(y0, y1, y2) > hy || Math.max(y0, y1, y2) < -hy) return false;
    if (Math.min(z0, z1, z2) > hz || Math.max(z0, z1, z2) < -hz) return false;

    // Triangle normal.
    const e0x = x1 - x0, e0y = y1 - y0, e0z = z1 - z0;
    const e1x = x2 - x1, e1y = y2 - y1, e1z = z2 - z1;
    const nx = e0y * e1z - e0z * e1y, ny = e0z * e1x - e0x * e1z, nz = e0x * e1y - e0y * e1x;
    if (Math.abs(nx * x0 + ny * y0 + nz * z0) > hx * Math.abs(nx) + hy * Math.abs(ny) + hz * Math.abs(nz)) return false;

    // Edge x box axis.
    const e2x = x0 - x2, e2y = y0 - y2, e2z = z0 - z2;
    return (
        !edgeSeparates(e0x, e0y, e0z, x0, y0, z0, x1, y1, z1, x2, y2, z2, hx, hy, hz) &&
        !edgeSeparates(e1x, e1y, e1z, x0, y0, z0, x1, y1, z1, x2, y2, z2, hx, hy, hz) &&
        !edgeSeparates(e2x, e2y, e2z, x0, y0, z0, x1, y1, z1, x2, y2, z2, hx, hy, hz)
    );
}

/** Is one of the three axes `edge x (a box axis)` a separating one, for a triangle relative to the
 *  centre of a box with half sizes h? */
function edgeSeparates(
    ex: number, ey: number, ez: number,
    x0: number, y0: number, z0: number,
    x1: number, y1: number, z1: number,
    x2: number, y2: number, z2: number,
    hx: number, hy: number, hz: number,
): boolean {
    // edge x X = (0, ez, -ey)
    let p0 = ez * y0 - ey * z0, p1 = ez * y1 - ey * z1, p2 = ez * y2 - ey * z2;
    let rad = hy * Math.abs(ez) + hz * Math.abs(ey);
    if (Math.min(p0, p1, p2) > rad || Math.max(p0, p1, p2) < -rad) return true;
    // edge x Y = (-ez, 0, ex)
    p0 = ex * z0 - ez * x0, p1 = ex * z1 - ez * x1, p2 = ex * z2 - ez * x2;
    rad = hx * Math.abs(ez) + hz * Math.abs(ex);
    if (Math.min(p0, p1, p2) > rad || Math.max(p0, p1, p2) < -rad) return true;
    // edge x Z = (ey, -ex, 0)
    p0 = ey * x0 - ex * y0, p1 = ey * x1 - ex * y1, p2 = ey * x2 - ex * y2;
    rad = hx * Math.abs(ey) + hy * Math.abs(ex);
    return Math.min(p0, p1, p2) > rad || Math.max(p0, p1, p2) < -rad;
}
