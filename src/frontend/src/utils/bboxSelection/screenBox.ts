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

import {inside, project, rectAgainstBounds, RectVsBounds, triangleMeetsRect, type NdcRect, type Projected, type ScreenBoxMode} from "./screenRect";

export {ndcRect, screenBoxMode, triangleMeetsRect, type NdcRect, type ScreenBoxMode} from "./screenRect";

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
        const how = rectAgainstBounds(b, o, m, rect);
        if (how === RectVsBounds.Outside) continue;
        if (how === RectVsBounds.Inside) {
            out.push(id);
            continue;
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
