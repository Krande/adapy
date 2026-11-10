/**
 * A rectangle drawn on the screen and what it means, as plain numbers: no three.js, so both
 * viewports and their tests share it. Dragged left to right the rectangle is a WINDOW (it takes an
 * element wholly inside it), right to left a CROSSING (an element that touches it).
 */

export type ScreenBoxMode = "window" | "crossing";

/** A rectangle in normalised device coordinates, x0 < x1 and y0 < y1. */
export interface NdcRect {
    x0: number;
    y0: number;
    x1: number;
    y1: number;
}

/** Window when dragged rightwards, crossing when dragged leftwards. */
export function screenBoxMode(startX: number, endX: number): ScreenBoxMode {
    return endX >= startX ? "window" : "crossing";
}

/** The rectangle between two client points, in NDC of the element `rect`. */
export function ndcRect(a: {x: number; y: number}, b: {x: number; y: number}, rect: DOMRect): NdcRect {
    const nx = (x: number) => ((x - rect.left) / rect.width) * 2 - 1;
    const ny = (y: number) => -((y - rect.top) / rect.height) * 2 + 1;
    const xs = [nx(a.x), nx(b.x)].sort((p, q) => p - q);
    const ys = [ny(a.y), ny(b.y)].sort((p, q) => p - q);
    return {x0: xs[0], x1: xs[1], y0: ys[0], y1: ys[1]};
}

/** A projected point: NDC x, y, and whether it is in front of the camera. */
export type Projected = [number, number, boolean];

export function project(m: ArrayLike<number>, x: number, y: number, z: number): Projected {
    const w = m[3] * x + m[7] * y + m[11] * z + m[15];
    if (w <= 1e-9) return [0, 0, false];
    return [(m[0] * x + m[4] * y + m[8] * z + m[12]) / w, (m[1] * x + m[5] * y + m[9] * z + m[13]) / w, true];
}

export const inside = (p: Projected, r: NdcRect) => p[2] && p[0] >= r.x0 && p[0] <= r.x1 && p[1] >= r.y0 && p[1] <= r.y1;

/** Does the 2D triangle a, b, c meet the rectangle? Separating axes: the rectangle's two and the
 *  triangle's three edge normals. */
export function triangleMeetsRect(a: Projected, b: Projected, c: Projected, r: NdcRect): boolean {
    if (Math.max(a[0], b[0], c[0]) < r.x0 || Math.min(a[0], b[0], c[0]) > r.x1) return false;
    if (Math.max(a[1], b[1], c[1]) < r.y0 || Math.min(a[1], b[1], c[1]) > r.y1) return false;
    const corners: [number, number][] = [[r.x0, r.y0], [r.x1, r.y0], [r.x1, r.y1], [r.x0, r.y1]];
    const tri = [a, b, c];
    for (let i = 0; i < 3; i++) {
        const p = tri[i], q = tri[(i + 1) % 3], o = tri[(i + 2) % 3];
        const nx = q[1] - p[1], ny = p[0] - q[0];
        const side = nx * (o[0] - p[0]) + ny * (o[1] - p[1]);
        // Every corner on the far side of this edge from the triangle's third vertex: separated.
        if (corners.every(([x, y]) => (nx * (x - p[0]) + ny * (y - p[1])) * side < 0)) return false;
    }
    return true;
}

/** How a rectangle stands to a world box seen through the camera. */
export const RectVsBounds = {
    /** The whole box is in front of the camera and its picture misses the rectangle. */
    Outside: 0,
    /** The whole box is in front of the camera and its picture is inside the rectangle. */
    Inside: 1,
    /** In front of the camera, partly inside: only the geometry in the box can tell. */
    Straddles: 2,
    /** The box reaches behind the camera, so it has no picture to compare. */
    Behind: 3,
} as const;
export type RectVsBounds = (typeof RectVsBounds)[keyof typeof RectVsBounds];

/** The rectangle against the world box at `o` in `b` (min x, y, z, max x, y, z), through `m`, the
 *  camera's projection x view matrix (column-major). Its eight corners are projected; nothing is
 *  made per call, which is once per element of the model. */
export function rectAgainstBounds(b: ArrayLike<number>, o: number, m: ArrayLike<number>, rect: NdcRect): RectVsBounds {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (let c = 0; c < 8; c++) {
        const x = b[o + (c & 1 ? 3 : 0)], y = b[o + 1 + (c & 2 ? 3 : 0)], z = b[o + 2 + (c & 4 ? 3 : 0)];
        const w = m[3] * x + m[7] * y + m[11] * z + m[15];
        if (w <= 1e-9) return RectVsBounds.Behind;
        const px = (m[0] * x + m[4] * y + m[8] * z + m[12]) / w, py = (m[1] * x + m[5] * y + m[9] * z + m[13]) / w;
        x0 = Math.min(x0, px);
        x1 = Math.max(x1, px);
        y0 = Math.min(y0, py);
        y1 = Math.max(y1, py);
    }
    if (x1 < rect.x0 || x0 > rect.x1 || y1 < rect.y0 || y0 > rect.y1) return RectVsBounds.Outside;
    if (x0 >= rect.x0 && x1 <= rect.x1 && y0 >= rect.y0 && y1 <= rect.y1) return RectVsBounds.Inside;
    return RectVsBounds.Straddles;
}

/** Does the rectangle take an element known only by its world box (at `o` in `b`)? A window takes it
 *  when the box's picture is wholly inside, a crossing when the picture touches the rectangle. That is
 *  the answer of a viewport that keeps no triangles to look at: for an element whose box straddles
 *  the rectangle it is coarser than deciding on the geometry (a window may miss a slanted element
 *  that is itself inside, a crossing may take one that only its box's corner reaches). A box that
 *  reaches behind the camera is never in a window, and in a crossing when the part of it in front
 *  touches the rectangle. */
export function rectTakesBounds(b: ArrayLike<number>, o: number, m: ArrayLike<number>, rect: NdcRect, mode: ScreenBoxMode): boolean {
    const how = rectAgainstBounds(b, o, m, rect);
    if (how === RectVsBounds.Inside) return true;
    if (how === RectVsBounds.Outside || mode === "window") return false;
    if (how === RectVsBounds.Straddles) return true;
    // Behind: the corners in front of the camera stand for the part that can be seen.
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (let c = 0; c < 8; c++) {
        const x = b[o + (c & 1 ? 3 : 0)], y = b[o + 1 + (c & 2 ? 3 : 0)], z = b[o + 2 + (c & 4 ? 3 : 0)];
        const w = m[3] * x + m[7] * y + m[11] * z + m[15];
        if (w <= 1e-9) continue;
        const px = (m[0] * x + m[4] * y + m[8] * z + m[12]) / w, py = (m[1] * x + m[5] * y + m[9] * z + m[13]) / w;
        x0 = Math.min(x0, px);
        x1 = Math.max(x1, px);
        y0 = Math.min(y0, py);
        y1 = Math.max(y1, py);
    }
    return x0 <= rect.x1 && x1 >= rect.x0 && y0 <= rect.y1 && y1 >= rect.y0;
}
