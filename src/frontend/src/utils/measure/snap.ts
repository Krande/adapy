// Where a measuring click lands: snapped to the model's geometric features rather than to the
// vertices of its triangles. No three.js, no store, no viewport: the caller hands in the surface point
// under the pointer, the feature edges of the objects around it, and how a world point projects onto
// the screen.
//
// The features are the model's real edges -- the outline the viewer draws: the boundary of each
// object's surface and the folds sharper than 30 degrees (utils/mesh_select/edgeCore.ts on the
// three.js viewport, the renderer's own on the three.js-free one) -- not the triangulation's inner
// edges. From those segments:
//   - a CORNER is an end where they meet as a corner: where one, three or more segments end, or two
//     that turn by more than the corner angle (30 degrees). The ends along a curved edge's polyline
//     turn by a little each and are no corners;
//   - a MIDPOINT is the middle, by length, of an edge from one corner to the next (a run of segments
//     that is one straight or curved edge);
//   - an EDGE point is the point of a segment nearest the pointer.
// Tolerances are in screen pixels, so snapping feels the same at any zoom: a corner (or midpoint)
// within `cornerPx` of the pointer wins, else an edge within `edgePx`, else the surface point itself.
// A candidate further from the eye than the surface under the pointer (by more than a sliver) is
// behind it -- the far corner of a box seen through its face -- and is not taken.

import {lerp, norm, sub, type Vec3} from "./measureMath";

export type SnapKind = "corner" | "midpoint" | "edge" | "surface";

export interface SnapResult {
    kind: SnapKind;
    /** The snapped point, in the world. */
    world: [number, number, number];
    /** For an edge or a midpoint: the segment it lies on (for a midpoint, the edge's straight chord). */
    edge?: readonly [Vec3, Vec3];
}

/** An object's feature edges, ready to snap to. */
export interface FeatureGraph {
    segments: Float32Array;
    /** xyz per corner. */
    corners: Float32Array;
    /** The middle of each edge between corners, and the edge's two ends. */
    midpoints: {point: [number, number, number]; a: Vec3; b: Vec3}[];
}

/** A world point on the screen, in CSS pixels from the view's corner; null when it is behind the camera. */
export type Projector = (p: Vec3) => readonly [number, number] | null;

export const CORNER_TURN_DEG = 30;
export const CORNER_PX = 12;
export const EDGE_PX = 8;

/** Ends closer than this (world units) are one point: the segments' ends are 32-bit floats. */
const WELD = 1e-5;

/** Find the corners and edge midpoints of feature-edge segments (six numbers each). */
export function featureGraph(segments: Float32Array, cornerTurnDeg = CORNER_TURN_DEG): FeatureGraph {
    const ids = new Map<string, number>();
    const points: [number, number, number][] = [];
    const vertex = (o: number): number => {
        const key = `${Math.round(segments[o] / WELD)},${Math.round(segments[o + 1] / WELD)},${Math.round(segments[o + 2] / WELD)}`;
        let id = ids.get(key);
        if (id === undefined) {
            id = points.length;
            ids.set(key, id);
            points.push([segments[o], segments[o + 1], segments[o + 2]]);
        }
        return id;
    };
    const neighbours: number[][] = [];
    const seenPairs = new Set<string>();
    for (let o = 0; o + 5 < segments.length; o += 6) {
        const a = vertex(o), b = vertex(o + 3);
        if (a === b) continue;
        const pair = a < b ? `${a},${b}` : `${b},${a}`;
        if (seenPairs.has(pair)) continue;
        seenPairs.add(pair);
        (neighbours[a] ??= []).push(b);
        (neighbours[b] ??= []).push(a);
    }
    const cosTurn = Math.cos((cornerTurnDeg * Math.PI) / 180);
    const isCorner = points.map((p, i) => {
        const n = neighbours[i] ?? [];
        if (n.length !== 2) return n.length > 0;
        // Two segments: a corner when the path through here turns by more than the corner angle.
        const u = sub(p, points[n[0]]), v = sub(points[n[1]], p);
        const lu = norm(u), lv = norm(v);
        if (lu === 0 || lv === 0) return true;
        const straightness = (u[0] * v[0] + u[1] * v[1] + u[2] * v[2]) / (lu * lv);
        return straightness < cosTurn;
    });
    const corners: number[] = [];
    isCorner.forEach((c, i) => c && corners.push(...points[i]));

    // Each edge from a corner to the next, walked once: its middle by length.
    const midpoints: FeatureGraph["midpoints"] = [];
    const walked = new Set<string>();
    const key = (a: number, b: number) => (a < b ? `${a},${b}` : `${b},${a}`);
    isCorner.forEach((corner, start) => {
        if (!corner) return;
        for (const first of neighbours[start] ?? []) {
            if (walked.has(key(start, first))) continue;
            const chain = [start];
            let prev = start, at = first;
            walked.add(key(prev, at));
            while (!isCorner[at] && at !== start) {
                chain.push(at);
                const next = (neighbours[at] ?? []).find((n) => n !== prev && !walked.has(key(at, n)));
                if (next === undefined) break;
                walked.add(key(at, next));
                prev = at;
                at = next;
            }
            chain.push(at);
            const middle = chainMiddle(chain.map((i) => points[i]));
            if (middle) midpoints.push({point: middle, a: points[chain[0]], b: points[chain[chain.length - 1]]});
        }
    });
    return {segments, corners: Float32Array.from(corners), midpoints};
}

/** The point halfway along a polyline by length; null for one of no length. */
function chainMiddle(chain: readonly Vec3[]): [number, number, number] | null {
    let total = 0;
    for (let i = 1; i < chain.length; i++) total += norm(sub(chain[i], chain[i - 1]));
    if (total === 0) return null;
    let left = total / 2;
    for (let i = 1; i < chain.length; i++) {
        const leg = norm(sub(chain[i], chain[i - 1]));
        if (leg >= left) return lerp(chain[i - 1], chain[i], leg === 0 ? 0 : left / leg);
        left -= leg;
    }
    return [chain[chain.length - 1][0], chain[chain.length - 1][1], chain[chain.length - 1][2]];
}

export interface SnapOptions {
    cornerPx?: number;
    edgePx?: number;
    /** Where the camera is: what "behind the surface" is measured from. Without it, nothing is. */
    eye?: Vec3 | null;
    /** Off: the surface point, as it is. */
    snap?: boolean;
}

/** How much further than the surface (as a share of its distance) a candidate may be and still be on
 *  it: a corner of the face under the pointer lies at the surface's depth, give or take. */
const BEHIND_SHARE = 0.02;

/**
 * The point a click at `pointer` (CSS pixels in the view) measures: the nearest corner or edge
 * midpoint within `cornerPx`, else the nearest point on an edge within `edgePx`, else `surface` (the
 * world point under the pointer). Null when there is no surface and nothing to snap to.
 */
export function snapPoint(
    pointer: readonly [number, number],
    surface: Vec3 | null,
    graphs: readonly FeatureGraph[],
    project: Projector,
    options: SnapOptions = {},
): SnapResult | null {
    const surfaceResult: SnapResult | null = surface ? {kind: "surface", world: [surface[0], surface[1], surface[2]]} : null;
    if (options.snap === false) return surfaceResult;
    const cornerPx = options.cornerPx ?? CORNER_PX;
    const edgePx = options.edgePx ?? EDGE_PX;
    const eye = options.eye ?? null;
    const limit = eye && surface ? norm(sub(surface, eye)) * (1 + BEHIND_SHARE) : Infinity;
    const inFront = (p: Vec3) => limit === Infinity || norm(sub(p, eye!)) <= limit;
    const screenDistance = (p: Vec3): number => {
        const s = project(p);
        return s ? Math.hypot(s[0] - pointer[0], s[1] - pointer[1]) : Infinity;
    };

    // Corners first, then the middles of edges.
    let best: SnapResult | null = null;
    let bestPx = cornerPx;
    for (const g of graphs) {
        for (let i = 0; i + 2 < g.corners.length; i += 3) {
            const p: Vec3 = [g.corners[i], g.corners[i + 1], g.corners[i + 2]];
            const d = screenDistance(p);
            if (d <= bestPx && inFront(p)) {
                bestPx = d;
                best = {kind: "corner", world: [p[0], p[1], p[2]]};
            }
        }
    }
    if (best) return best;
    for (const g of graphs) {
        for (const m of g.midpoints) {
            const d = screenDistance(m.point);
            if (d <= bestPx && inFront(m.point)) {
                bestPx = d;
                best = {kind: "midpoint", world: [...m.point], edge: [m.a, m.b]};
            }
        }
    }
    if (best) return best;

    // Then the nearest point of an edge.
    bestPx = edgePx;
    for (const g of graphs) {
        const s = g.segments;
        for (let o = 0; o + 5 < s.length; o += 6) {
            const a: Vec3 = [s[o], s[o + 1], s[o + 2]], b: Vec3 = [s[o + 3], s[o + 4], s[o + 5]];
            const sa = project(a), sb = project(b);
            if (!sa || !sb) continue;
            // A cheap first look, on the screen.
            if (pointToSegment2d(pointer, sa, sb).distance > bestPx) continue;
            // Then exactly: the screen distance along the segment in the world has one minimum
            // (a line projects to a line), found by narrowing the interval around it.
            const t = nearestOnSegment(pointer, a, b, project);
            const p = lerp(a, b, t);
            const d = screenDistance(p);
            if (d <= bestPx && inFront(p)) {
                bestPx = d;
                best = {kind: "edge", world: p, edge: [a, b]};
            }
        }
    }
    return best ?? surfaceResult;
}

function pointToSegment2d(p: readonly [number, number], a: readonly [number, number], b: readonly [number, number]): {t: number; distance: number} {
    const abx = b[0] - a[0], aby = b[1] - a[1];
    const len2 = abx * abx + aby * aby;
    const t = len2 === 0 ? 0 : Math.max(0, Math.min(1, ((p[0] - a[0]) * abx + (p[1] - a[1]) * aby) / len2));
    return {t, distance: Math.hypot(a[0] + abx * t - p[0], a[1] + aby * t - p[1])};
}

/** The parameter (0..1) of the point of segment a-b whose projection is nearest `pointer`. */
function nearestOnSegment(pointer: readonly [number, number], a: Vec3, b: Vec3, project: Projector): number {
    const at = (t: number): number => {
        const s = project(lerp(a, b, t));
        return s ? Math.hypot(s[0] - pointer[0], s[1] - pointer[1]) : Infinity;
    };
    let lo = 0, hi = 1;
    for (let i = 0; i < 40; i++) {
        const m1 = lo + (hi - lo) / 3, m2 = hi - (hi - lo) / 3;
        if (at(m1) <= at(m2)) hi = m2;
        else lo = m1;
    }
    return (lo + hi) / 2;
}
