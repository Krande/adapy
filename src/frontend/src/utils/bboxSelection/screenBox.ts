/**
 * Selecting the elements a rectangle drawn on the screen takes, the way CAD tools do:
 * dragged left to right it is a WINDOW (an element wholly inside), right to left a
 * CROSSING (an element that touches it).
 *
 * Per element, its cached world AABB is projected first; only an element whose
 * projected box straddles the rectangle is decided on its own vertices (window) or
 * triangles (crossing), so a diagonal member is not taken for its bounding box.
 * Geometry behind the camera never counts as inside.
 */

import type * as THREE from "three";

import type {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {boxCutAway, type PlaneLike} from "@/utils/mesh_select/rangeVisibility";

import {rangeBounds} from "./rangeBounds";

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
type Projected = [number, number, boolean];

function project(m: ArrayLike<number>, x: number, y: number, z: number): Projected {
    const w = m[3] * x + m[7] * y + m[11] * z + m[15];
    if (w <= 1e-9) return [0, 0, false];
    return [(m[0] * x + m[4] * y + m[8] * z + m[12]) / w, (m[1] * x + m[5] * y + m[9] * z + m[13]) / w, true];
}

const inside = (p: Projected, r: NdcRect) => p[2] && p[0] >= r.x0 && p[0] <= r.x1 && p[1] >= r.y0 && p[1] <= r.y1;

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

/**
 * The ranges of `mesh` the rectangle takes, skipping `hidden` ones and any a section plane
 * (`planes`) cuts away whole -- what is not drawn is not taken. `viewProj` is the camera's
 * projection x view matrix.
 */
export function rangesInScreenRect(
    mesh: CustomBatchedMesh,
    viewProj: THREE.Matrix4,
    rect: NdcRect,
    mode: ScreenBoxMode,
    hidden: ReadonlySet<string> = new Set(),
    planes: readonly PlaneLike[] | null = null,
): string[] {
    const rb = rangeBounds(mesh);
    if (!rb) return [];
    const m = viewProj.elements;
    const geom = mesh.geometry as THREE.BufferGeometry;
    const idx = geom.index!.array;
    const p = (geom.attributes.position as THREE.BufferAttribute).array;
    const e = mesh.matrixWorld.elements;
    const vertex = (i: number): Projected => {
        const o = idx[i] * 3;
        const lx = p[o], ly = p[o + 1], lz = p[o + 2];
        return project(
            m,
            e[0] * lx + e[4] * ly + e[8] * lz + e[12],
            e[1] * lx + e[5] * ly + e[9] * lz + e[13],
            e[2] * lx + e[6] * ly + e[10] * lz + e[14],
        );
    };
    const out: string[] = [];
    const b = rb.bounds;
    for (let r = 0; r < rb.ids.length; r++) {
        const id = rb.ids[r];
        if (hidden.has(id)) continue;
        const o = r * 6;
        if (!Number.isFinite(b[o]) || boxCutAway(b, o, planes)) continue;
        // The projected AABB: decides most elements without touching their geometry.
        let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity, allFront = true;
        for (let c = 0; c < 8; c++) {
            const q = project(m, b[o + (c & 1 ? 3 : 0)], b[o + 1 + (c & 2 ? 3 : 0)], b[o + 2 + (c & 4 ? 3 : 0)]);
            if (!q[2]) {
                allFront = false;
                break;
            }
            x0 = Math.min(x0, q[0]);
            x1 = Math.max(x1, q[0]);
            y0 = Math.min(y0, q[1]);
            y1 = Math.max(y1, q[1]);
        }
        if (allFront) {
            if (x1 < rect.x0 || x0 > rect.x1 || y1 < rect.y0 || y0 > rect.y1) continue;
            if (x0 >= rect.x0 && x1 <= rect.x1 && y0 >= rect.y0 && y1 <= rect.y1) {
                out.push(id);
                continue;
            }
        }
        // Straddles the rectangle (or reaches behind the camera): decided on the geometry itself.
        const start = rb.starts[r], end = start + rb.counts[r];
        if (mode === "window") {
            let all = end > start;
            for (let i = start; i < end; i++) {
                if (!inside(vertex(i), rect)) {
                    all = false;
                    break;
                }
            }
            if (all) out.push(id);
            continue;
        }
        for (let i = start; i + 2 < end; i += 3) {
            const a = vertex(i), bb = vertex(i + 1), c = vertex(i + 2);
            if (!a[2] || !bb[2] || !c[2]) continue;
            if (triangleMeetsRect(a, bb, c, rect)) {
                out.push(id);
                break;
            }
        }
    }
    return out;
}
