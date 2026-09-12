/**
 * Pure GEOMETRY helpers for the builder scene.
 *
 * Owns: the ray/line closest-point solve a face drag needs, and the three
 * buffer geometries a loft band is drawn from (a proportional swept mesh, a
 * per-face-grouped one for face picking, and the two station ring outlines).
 * No store access and no scene state — geometry in, geometry out.
 */

import * as THREE from "three";

import type {Vec3} from "@/utils/cellbuilder/snap";

export function lineParamFromRay(ray: THREE.Ray, lineOrigin: THREE.Vector3, lineDir: THREE.Vector3): number | null {
    // Closest-point parameter along the (unit) line for the pointer ray.
    const w0 = new THREE.Vector3().subVectors(ray.origin, lineOrigin);
    const b = ray.direction.dot(lineDir);
    const denom = 1 - b * b;
    if (Math.abs(denom) < 1e-6) return null; // ray ~parallel to the drag axis
    const d = ray.direction.dot(w0);
    const e = lineDir.dot(w0);
    return (e - b * d) / denom;
}


// --- Loft band geometry (read-only swept proxy) ----------------------------
// Build a translucent swept mesh between a band's two profile rings (model-space
// absolute points — the mesh sits at the container origin, which carries the
// model offset, exactly like box cells). Side walls pair the two rings around
// the loop (equal ring counts = a clean quad strip; differing counts pair by
// proportional index so a rectangle->circle band still closes without crashing).
// Convex end caps (fans) close the bay cheaply. DoubleSide so winding is moot.
export function sweptBandGeometry(lo: Vec3[], hi: Vec3[]): THREE.BufferGeometry {
    const n0 = lo.length;
    const n1 = hi.length;
    const positions: number[] = [];
    for (const p of lo) positions.push(p[0], p[1], p[2]);
    for (const p of hi) positions.push(p[0], p[1], p[2]);
    const hiBase = n0;
    const indices: number[] = [];
    const K = Math.max(n0, n1);
    for (let k = 0; k < K; k++) {
        const a0 = Math.floor((k * n0) / K) % n0;
        const a1 = Math.floor(((k + 1) * n0) / K) % n0;
        const b0 = hiBase + (Math.floor((k * n1) / K) % n1);
        const b1 = hiBase + (Math.floor(((k + 1) * n1) / K) % n1);
        indices.push(a0, a1, b1, a0, b1, b0);
    }
    // End caps: fan-triangulate each (convex) ring.
    for (let i = 1; i < n0 - 1; i++) indices.push(0, i, i + 1);
    for (let i = 1; i < n1 - 1; i++) indices.push(hiBase, hiBase + i, hiBase + i + 1);
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geo.setIndex(indices);
    geo.computeVertexNormals();
    return geo;
}

// Per-face-pickable swept mesh (Phase 3b): one geometry group per profile side
// panel (edge k = ring vertex k -> k+1), plus a cap_lo fan and a cap_hi fan, so
// a raycast face's materialIndex maps 1:1 to a loft face id in the order
// bandFaceIds returns (edge0..edge_{n-1}, cap_lo, cap_hi). Requires equal ring
// counts (the homogeneous rectangle/circle bands the backend numbers); returns
// null for mismatched rings so the caller degrades to the single-material
// proportional mesh (whole-band pick only).
export function sweptBandGroupedGeometry(lo: Vec3[], hi: Vec3[]): THREE.BufferGeometry | null {
    const n = lo.length;
    if (n < 3 || hi.length !== n) return null;
    const positions: number[] = [];
    for (const p of lo) positions.push(p[0], p[1], p[2]);
    for (const p of hi) positions.push(p[0], p[1], p[2]);
    const hiBase = n;
    const indices: number[] = [];
    const geo = new THREE.BufferGeometry();
    let cursor = 0;
    // Side panels: material index k = profile edge k.
    for (let k = 0; k < n; k++) {
        const a0 = k;
        const a1 = (k + 1) % n;
        const b0 = hiBase + k;
        const b1 = hiBase + ((k + 1) % n);
        indices.push(a0, a1, b1, a0, b1, b0);
        geo.addGroup(cursor, 6, k);
        cursor += 6;
    }
    // cap_lo fan (material n), cap_hi fan (material n+1).
    const capLoStart = cursor;
    for (let i = 1; i < n - 1; i++) {
        indices.push(0, i, i + 1);
        cursor += 3;
    }
    geo.addGroup(capLoStart, cursor - capLoStart, n);
    const capHiStart = cursor;
    for (let i = 1; i < n - 1; i++) {
        indices.push(hiBase, hiBase + i, hiBase + i + 1);
        cursor += 3;
    }
    geo.addGroup(capHiStart, cursor - capHiStart, n + 1);
    geo.setAttribute("position", new THREE.Float32BufferAttribute(positions, 3));
    geo.setIndex(indices);
    geo.computeVertexNormals();
    return geo;
}

// Line segments tracing the two closed station rings (the band's edge overlay).
export function ringsEdgesGeometry(lo: Vec3[], hi: Vec3[]): THREE.BufferGeometry {
    const pts: number[] = [];
    for (const ring of [lo, hi]) {
        const n = ring.length;
        for (let i = 0; i < n; i++) {
            const a = ring[i];
            const b = ring[(i + 1) % n];
            pts.push(a[0], a[1], a[2], b[0], b[1], b[2]);
        }
    }
    const geo = new THREE.BufferGeometry();
    geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
    return geo;
}
