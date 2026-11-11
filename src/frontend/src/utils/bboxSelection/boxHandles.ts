/**
 * What a selection box looks like and how its handles are dragged, as plain numbers: the box's
 * edges, a handle in the middle of each face, an arrow per axis from its centre, and the maths that
 * turns a pointer on the screen into a distance along an axis. No three.js: the three.js-free
 * viewport draws and drags a box with these, and they are tested without a renderer.
 *
 * `m` is always the camera's projection x view matrix (column-major, 16 numbers), and a point on the
 * screen is in normalised device coordinates (x and y from -1 to 1, y up).
 */

import {BOX_FACES, boxCenter, type Aabb, type Vec3} from "./boxMath";

/** The colour of a handle by its axis (x, y, z), as on the three.js viewport. */
export const HANDLE_COLORS = ["#e5484d", "#30a46c", "#3e63dd"] as const;

/** The box's twelve edges as line segments: xyz per point, two points per edge. */
export function boxEdges(b: Aabb): Float32Array {
    const out = new Float32Array(72);
    let n = 0;
    const corner = (c: number, k: number) => (c & (1 << k) ? b.max[k] : b.min[k]);
    for (let c = 0; c < 8; c++) {
        for (let k = 0; k < 3; k++) {
            // From each corner along each axis, once: only from the low end.
            if (c & (1 << k)) continue;
            const d = c | (1 << k);
            out[n++] = corner(c, 0);
            out[n++] = corner(c, 1);
            out[n++] = corner(c, 2);
            out[n++] = corner(d, 0);
            out[n++] = corner(d, 1);
            out[n++] = corner(d, 2);
        }
    }
    return out;
}

/** The box's six faces as triangles: its eight corners (xyz each) and three indices per triangle. */
export function boxFaces(b: Aabb): {points: Float32Array; indices: Uint32Array} {
    const points = new Float32Array(24);
    for (let c = 0; c < 8; c++) {
        for (let k = 0; k < 3; k++) points[c * 3 + k] = c & (1 << k) ? b.max[k] : b.min[k];
    }
    const indices = new Uint32Array(36);
    let n = 0;
    for (let k = 0; k < 3; k++) {
        const u = 1 << ((k + 1) % 3), v = 1 << ((k + 2) % 3);
        for (const base of [0, 1 << k]) {
            indices.set([base, base | u, base | u | v, base, base | u | v, base | v], n);
            n += 6;
        }
    }
    return {points, indices};
}

/** The unit vector of axis 0, 1 or 2. */
export function axisVector(axis: number): Vec3 {
    return [axis === 0 ? 1 : 0, axis === 1 ? 1 : 0, axis === 2 ? 1 : 0];
}

/** Where a world point is on the screen; null behind the camera. */
export function toScreen(m: ArrayLike<number>, p: ArrayLike<number>): {x: number; y: number} | null {
    const w = m[3] * p[0] + m[7] * p[1] + m[11] * p[2] + m[15];
    if (w <= 1e-9) return null;
    return {x: (m[0] * p[0] + m[4] * p[1] + m[8] * p[2] + m[12]) / w, y: (m[1] * p[0] + m[5] * p[1] + m[9] * p[2] + m[13]) / w};
}

/**
 * How far along the world line `origin + t * dir` the pointer is: the `t` whose point is drawn
 * nearest the pointer, measured on the screen (`aspect` is the view's width over its height, so
 * near is near in pixels). Dragging a handle along its axis moves it by the difference between
 * this at the press and at each move, which keeps the handle under the pointer in perspective too.
 * Null when the line is seen end on, or the pointer is beyond where the line meets the horizon.
 */
export function lineParamAtScreen(m: ArrayLike<number>, origin: ArrayLike<number>, dir: ArrayLike<number>, x: number, y: number, aspect: number): number | null {
    // The line in clip space is A + t B; on the screen (x scaled to pixels' proportions) it is
    // (aspect (Ax + t Bx), Ay + t By) / (Aw + t Bw).
    const ax = (m[0] * origin[0] + m[4] * origin[1] + m[8] * origin[2] + m[12]) * aspect;
    const ay = m[1] * origin[0] + m[5] * origin[1] + m[9] * origin[2] + m[13];
    const aw = m[3] * origin[0] + m[7] * origin[1] + m[11] * origin[2] + m[15];
    const bx = (m[0] * dir[0] + m[4] * dir[1] + m[8] * dir[2]) * aspect;
    const by = m[1] * dir[0] + m[5] * dir[1] + m[9] * dir[2];
    const bw = m[3] * dir[0] + m[7] * dir[1] + m[11] * dir[2];
    if (aw <= 1e-9) return null;
    // The line's direction on the screen, at the origin.
    let dx = bx * aw - ax * bw, dy = by * aw - ay * bw;
    const length = Math.hypot(dx, dy);
    if (length < 1e-9 * aw * aw) return null;
    dx /= length;
    dy /= length;
    // The pointer's place along that direction; t puts the line's point at the same place.
    const along = dx * x * aspect + dy * y;
    const denominator = dx * bx + dy * by - bw * along;
    if (Math.abs(denominator) < 1e-12) return null;
    const t = (aw * along - (dx * ax + dy * ay)) / denominator;
    return aw + t * bw > 1e-9 ? t : null;
}

/** Is the point on the screen over the box (over the picture of any of its faces)? False for a box
 *  that reaches behind the camera. */
export function screenPointOverBox(m: ArrayLike<number>, b: Aabb, x: number, y: number): boolean {
    const px = new Float64Array(8), py = new Float64Array(8);
    for (let c = 0; c < 8; c++) {
        const s = toScreen(m, [c & 1 ? b.max[0] : b.min[0], c & 2 ? b.max[1] : b.min[1], c & 4 ? b.max[2] : b.min[2]]);
        if (!s) return false;
        px[c] = s.x;
        py[c] = s.y;
    }
    // Which side of the edge i -> j the point is on.
    const side = (i: number, j: number) => (px[j] - px[i]) * (y - py[i]) - (py[j] - py[i]) * (x - px[i]);
    for (let k = 0; k < 3; k++) {
        const u = 1 << ((k + 1) % 3), v = 1 << ((k + 2) % 3);
        for (const base of [0, 1 << k]) {
            // A face's picture is a convex quadrilateral: the point is in it when it is on one side
            // of all four edges, taken in order around it.
            const q = [base, base | u, base | u | v, base | v];
            let positive = 0, negative = 0;
            for (let e = 0; e < 4; e++) {
                const s = side(q[e], q[(e + 1) % 4]);
                if (s > 0) positive++;
                else if (s < 0) negative++;
            }
            if (!positive || !negative) return true;
        }
    }
    return false;
}

/** The thinnest a box may be dragged: a face cannot pass the opposite one. */
export function minBoxSize(b: Aabb): number {
    return Math.max(1e-3, 0.001 * Math.max(b.max[0] - b.min[0], b.max[1] - b.min[1], b.max[2] - b.min[2]));
}

/** The arrows that move the whole box: one per axis, from its centre, `length` long. Each is its
 *  axis, its base and its tip. */
export function moveArrows(b: Aabb, length: number): {axis: number; base: Vec3; tip: Vec3}[] {
    const base = boxCenter(b);
    return [0, 1, 2].map((axis) => {
        const tip: Vec3 = [...base];
        tip[axis] += length;
        return {axis, base, tip};
    });
}

/** The axis a face's handle moves along. */
export const faceAxis = (face: number): number => BOX_FACES[face]?.axis ?? 0;
