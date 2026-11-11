/**
 * Per-element world-space bounds of a CustomBatchedMesh, and which elements a
 * selection box takes.
 *
 * An element is a draw range into the mesh's index buffer. Its AABB is computed
 * once per mesh and cached until the mesh's world matrix or geometry changes.
 * Positions are the base positions: a morph (FEA deformation) is not applied.
 */

import * as THREE from "three";

import type {CustomBatchedMesh} from "@/utils/mesh_select/CustomBatchedMesh";
import {boxCutAway, type PlaneLike} from "@/utils/mesh_select/rangeVisibility";

import {containedAt, overlapsAt, triangleTouchesBoxAt, type Aabb, type BoxSelectMode} from "./boxMath";

interface RangeBounds {
    ids: ReadonlyArray<string>;
    /** min xyz, max xyz per range, in `ids` order. */
    bounds: Float32Array;
    starts: Uint32Array;
    counts: Uint32Array;
    matrix: Float64Array;
    geometryId: number;
    positionVersion: number;
}

const cache = new WeakMap<CustomBatchedMesh, RangeBounds>();

function sameMatrix(a: Float64Array, m: THREE.Matrix4): boolean {
    const e = m.elements;
    for (let i = 0; i < 16; i++) if (a[i] !== e[i]) return false;
    return true;
}

/** The cached per-range AABBs of `mesh`, (re)computed when stale. */
export function rangeBounds(mesh: CustomBatchedMesh): RangeBounds | null {
    const geom = mesh.geometry as THREE.BufferGeometry;
    const index = geom.index;
    const pos = geom.attributes.position as THREE.BufferAttribute | undefined;
    if (!index || !pos) return null;
    mesh.updateWorldMatrix(true, false);
    const hit = cache.get(mesh);
    if (hit && hit.geometryId === geom.id && hit.positionVersion === pos.version && sameMatrix(hit.matrix, mesh.matrixWorld)) {
        return hit;
    }
    const segs = mesh.getSortedSegments();
    const n = segs.ids.length;
    const bounds = new Float32Array(n * 6);
    const idx = index.array;
    const p = pos.array;
    const e = mesh.matrixWorld.elements;
    for (let r = 0; r < n; r++) {
        let x0 = Infinity, y0 = Infinity, z0 = Infinity, x1 = -Infinity, y1 = -Infinity, z1 = -Infinity;
        const end = segs.starts[r] + segs.counts[r];
        for (let i = segs.starts[r]; i < end; i++) {
            const v = idx[i] * 3;
            const lx = p[v], ly = p[v + 1], lz = p[v + 2];
            const x = e[0] * lx + e[4] * ly + e[8] * lz + e[12];
            const y = e[1] * lx + e[5] * ly + e[9] * lz + e[13];
            const z = e[2] * lx + e[6] * ly + e[10] * lz + e[14];
            if (x < x0) x0 = x;
            if (y < y0) y0 = y;
            if (z < z0) z0 = z;
            if (x > x1) x1 = x;
            if (y > y1) y1 = y;
            if (z > z1) z1 = z;
        }
        bounds.set([x0, y0, z0, x1, y1, z1], r * 6);
    }
    const entry: RangeBounds = {
        ids: segs.ids,
        bounds,
        starts: segs.starts,
        counts: segs.counts,
        matrix: Float64Array.from(e),
        geometryId: geom.id,
        positionVersion: pos.version,
    };
    cache.set(mesh, entry);
    return entry;
}

/** The world AABB spanning the given ranges of `mesh`, or null when none is known. */
export function boundsOfRanges(mesh: CustomBatchedMesh, rangeIds: Iterable<string>): Aabb | null {
    const rb = rangeBounds(mesh);
    if (!rb) return null;
    const want = new Set(rangeIds);
    let out: Aabb | null = null;
    for (let r = 0; r < rb.ids.length; r++) {
        if (!want.has(rb.ids[r])) continue;
        const o = r * 6;
        if (!Number.isFinite(rb.bounds[o])) continue;
        if (!out) {
            out = {min: [rb.bounds[o], rb.bounds[o + 1], rb.bounds[o + 2]], max: [rb.bounds[o + 3], rb.bounds[o + 4], rb.bounds[o + 5]]};
            continue;
        }
        for (let k = 0; k < 3; k++) {
            out.min[k] = Math.min(out.min[k], rb.bounds[o + k]);
            out.max[k] = Math.max(out.max[k], rb.bounds[o + 3 + k]);
        }
    }
    return out;
}

/** The ranges of `mesh` the box takes in `mode`, skipping `hidden` ones and any a section plane
 *  (`planes`) cuts away whole. */
export function rangesInBox(
    mesh: CustomBatchedMesh,
    box: Aabb,
    mode: BoxSelectMode,
    hidden: ReadonlySet<string> = new Set(),
    planes: readonly PlaneLike[] | null = null,
): string[] {
    const rb = rangeBounds(mesh);
    if (!rb) return [];
    const out: string[] = [];
    const geom = mesh.geometry as THREE.BufferGeometry;
    const idx = geom.index!.array;
    const p = (geom.attributes.position as THREE.BufferAttribute).array;
    const e = mesh.matrixWorld.elements;
    for (let r = 0; r < rb.ids.length; r++) {
        const id = rb.ids[r];
        if (hidden.has(id) || boxCutAway(rb.bounds, r * 6, planes) || !overlapsAt(rb.bounds, r, box)) continue;
        if (containedAt(rb.bounds, r, box)) {
            out.push(id);
            continue;
        }
        if (mode === "full") continue;
        // Straddles the boundary: taken when any of its triangles touches the box.
        const end = rb.starts[r] + rb.counts[r];
        for (let i = rb.starts[r]; i + 2 < end; i += 3) {
            // The triangle's corners in the world, as plain numbers: nothing is made per triangle.
            const a = idx[i] * 3, b = idx[i + 1] * 3, c = idx[i + 2] * 3;
            const ax = p[a], ay = p[a + 1], az = p[a + 2];
            const bx = p[b], by = p[b + 1], bz = p[b + 2];
            const cx = p[c], cy = p[c + 1], cz = p[c + 2];
            if (triangleTouchesBoxAt(
                e[0] * ax + e[4] * ay + e[8] * az + e[12], e[1] * ax + e[5] * ay + e[9] * az + e[13], e[2] * ax + e[6] * ay + e[10] * az + e[14],
                e[0] * bx + e[4] * by + e[8] * bz + e[12], e[1] * bx + e[5] * by + e[9] * bz + e[13], e[2] * bx + e[6] * by + e[10] * bz + e[14],
                e[0] * cx + e[4] * cy + e[8] * cz + e[12], e[1] * cx + e[5] * cy + e[9] * cz + e[13], e[2] * cx + e[6] * cy + e[10] * cz + e[14],
                box,
            )) {
                out.push(id);
                break;
            }
        }
    }
    return out;
}
