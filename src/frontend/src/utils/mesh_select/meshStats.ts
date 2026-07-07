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
}

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

    return {
        triangles,
        vertices: seen.size,
        sizeX: size.x,
        sizeY: size.y,
        sizeZ: size.z,
        volume,
        area,
        density,
    };
}
