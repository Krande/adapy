import * as THREE from "three";
import {CustomBatchedMesh} from "./CustomBatchedMesh";

// Per-geom (draw-range) mesh statistics, computed client-side from the
// batched geometry buffer. Shared by the gallery "density" walk order
// (sort by triangles-per-area) and the Object Info "Mesh" section.
//
// A geom is one draw-range: [start, count] into the shared index
// buffer. Triangles = count / 3. "density" here is triangles per unit
// of surface AREA (m²) — the natural measure for the shell/plate
// meshes the viewer shows; volume is reported too for solids. Both are
// in local mesh space (metres in adapy's world).
export interface RangeStats {
    triangles: number;
    vertices: number; // distinct vertices referenced by this range
    sizeX: number;
    sizeY: number;
    sizeZ: number;
    volume: number; // bounding-box volume, m³
    area: number; // summed triangle area, m²
    density: number; // triangles / area (0 when area is 0)
    // "Crows-nest" spike score: how far the worst OUTLIER vertex sits from the mesh body, as a
    // multiple of the median vertex→centroid distance. A vertex shooting out past the geometry (the
    // crows-nest bug) scores high; benign geometry — even a deep thin extrusion whose side triangles
    // reach right across the bbox, or a coarse curved surface — has all its vertices ON the body, so
    // it scores ~1. Ranking by this walks the real spikes first. 0 for a tiny/degenerate mesh.
    maxSpike: number;
    spikeTris: number; // thin triangles that touch an outlier vertex (dist > SPIKE_OUTLIER_K · median)
}

// A triangle is "thin" (needle-like) once longest-edge² / (2·area) exceeds this.
const SPIKE_ASPECT_MIN = 8;
// A vertex is an OUTLIER (crows-nest spike) once its distance from the robust (median) centroid
// exceeds this multiple of the median vertex distance. A compact body sits at ~1–3×; a genuine
// spike is far out. This is what a raw edge/bbox reach test can't do — a deep extrusion's long side
// edges look identical to a spike by length, but their vertices aren't outliers.
export const SPIKE_OUTLIER_K = 4;

export function computeRangeStats(mesh: CustomBatchedMesh, rangeId: string): RangeStats | null {
    const geometry = mesh.geometry as THREE.BufferGeometry;
    const pos = geometry.getAttribute("position") as THREE.BufferAttribute | undefined;
    const indexAttr = geometry.getIndex();
    const range = mesh.drawRanges.get(rangeId);
    if (!pos || !indexAttr || !range) return null;

    const [start, count] = range;
    const idx = indexAttr.array as Uint16Array | Uint32Array;
    const end = Math.min(start + count, idx.length);

    const bbox = new THREE.Box3();
    const seen = new Set<number>();
    const a = new THREE.Vector3();
    const b = new THREE.Vector3();
    const c = new THREE.Vector3();
    const ab = new THREE.Vector3();
    const ac = new THREE.Vector3();
    const cross = new THREE.Vector3();
    const bc = new THREE.Vector3();
    let area = 0;

    for (let i = start; i + 2 < end; i += 3) {
        const ia = idx[i];
        const ib = idx[i + 1];
        const ic = idx[i + 2];
        a.fromBufferAttribute(pos, ia);
        b.fromBufferAttribute(pos, ib);
        c.fromBufferAttribute(pos, ic);
        bbox.expandByPoint(a);
        bbox.expandByPoint(b);
        bbox.expandByPoint(c);
        seen.add(ia);
        seen.add(ib);
        seen.add(ic);
        ab.subVectors(b, a);
        ac.subVectors(c, a);
        area += 0.5 * cross.crossVectors(ab, ac).length();
    }

    const size = new THREE.Vector3();
    if (!bbox.isEmpty()) bbox.getSize(size);
    const triangles = Math.floor(count / 3);
    const volume = size.x * size.y * size.z;
    const density = area > 0 ? triangles / area : 0;

    // --- crows-nest spike detection (outlier vertices) ---------------------------------------
    // Robust (median-per-axis) centroid, then median vertex→centroid distance as the body scale. A
    // vertex whose distance exceeds SPIKE_OUTLIER_K × that median is a spike (shot out past the body).
    const verts = [...seen];
    const median = (arr: number[]): number => {
        if (arr.length === 0) return 0;
        const s = arr.slice().sort((x, y) => x - y);
        const m = s.length >> 1;
        return s.length % 2 ? s[m] : 0.5 * (s[m - 1] + s[m]);
    };
    const xs: number[] = [];
    const ys: number[] = [];
    const zs: number[] = [];
    for (const vi of verts) {
        xs.push(pos.getX(vi));
        ys.push(pos.getY(vi));
        zs.push(pos.getZ(vi));
    }
    const cx = median(xs);
    const cy = median(ys);
    const cz = median(zs);
    const dists = verts.map((_, k) => Math.hypot(xs[k] - cx, ys[k] - cy, zs[k] - cz));
    const medDist = median(dists);
    let maxSpike = 0;
    const outliers = new Set<number>();
    if (medDist > 1e-9) {
        for (let k = 0; k < verts.length; k++) {
            const ratio = dists[k] / medDist;
            if (ratio > maxSpike) maxSpike = ratio;
            if (ratio > SPIKE_OUTLIER_K) outliers.add(verts[k]);
        }
    }

    // Count thin triangles that touch an outlier vertex — only when outliers exist.
    let spikeTris = 0;
    if (outliers.size) {
        for (let i = start; i + 2 < end; i += 3) {
            const ia = idx[i];
            const ib = idx[i + 1];
            const ic = idx[i + 2];
            if (!outliers.has(ia) && !outliers.has(ib) && !outliers.has(ic)) continue;
            a.fromBufferAttribute(pos, ia);
            b.fromBufferAttribute(pos, ib);
            c.fromBufferAttribute(pos, ic);
            ab.subVectors(b, a);
            ac.subVectors(c, a);
            bc.subVectors(c, b);
            const triArea = 0.5 * cross.crossVectors(ab, ac).length();
            const emax = Math.max(ab.length(), ac.length(), bc.length());
            if (triArea > 0 && emax * emax > SPIKE_ASPECT_MIN * 2 * triArea) spikeTris++;
        }
    }

    return {
        triangles,
        vertices: seen.size,
        sizeX: size.x,
        sizeY: size.y,
        sizeZ: size.z,
        volume,
        area,
        density,
        maxSpike,
        spikeTris,
    };
}
