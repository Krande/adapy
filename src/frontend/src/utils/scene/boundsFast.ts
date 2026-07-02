import * as THREE from "three";

// Fast geometry bounds.
//
// three.js's BufferGeometry.computeBoundingBox / computeBoundingSphere
// iterate vertices via ``vector.fromBufferAttribute(position, i)`` →
// getX/getY/getZ — one method call + temp-vector write per component. On a
// 70M-vertex model that per-vertex object churn dominates load (it showed
// up as the top self-time frames: fromBufferAttribute / getX/getY/getZ /
// computeBoundingSphere). A tight loop straight over the backing
// Float32Array is several times faster and allocation-free.
//
// Setting both boundingBox AND boundingSphere here means:
//   * Box3().setFromObject (the recenter step) reuses boundingBox instead
//     of forcing computeBoundingBox, and
//   * the renderer's first-frame frustum culling reuses boundingSphere
//     instead of computing it lazily (another full iteration).
//
// The sphere is derived exactly as three.js does (centre = box centre,
// radius = max vertex distance), so culling behaviour is unchanged.
// Interleaved or non-Float32 buffers fall back to the stock path.

export function fastComputeBounds(geom: THREE.BufferGeometry): void {
    if (geom.boundingBox && geom.boundingSphere) return;
    const pos = geom.getAttribute("position") as THREE.BufferAttribute | undefined;
    if (!pos) return;

    const arr = pos.array as ArrayLike<number>;
    const interleaved = (pos as unknown as {isInterleavedBufferAttribute?: boolean}).isInterleavedBufferAttribute;
    // Fast path only for a plain, tightly-packed xyz Float32 buffer.
    if (interleaved || !(arr instanceof Float32Array) || pos.itemSize !== 3) {
        if (!geom.boundingBox) geom.computeBoundingBox();
        if (!geom.boundingSphere) geom.computeBoundingSphere();
        return;
    }

    const n = arr.length;
    let minx = Infinity, miny = Infinity, minz = Infinity;
    let maxx = -Infinity, maxy = -Infinity, maxz = -Infinity;
    for (let i = 0; i < n; i += 3) {
        const x = arr[i], y = arr[i + 1], z = arr[i + 2];
        if (x < minx) minx = x;
        if (y < miny) miny = y;
        if (z < minz) minz = z;
        if (x > maxx) maxx = x;
        if (y > maxy) maxy = y;
        if (z > maxz) maxz = z;
    }
    if (!isFinite(minx)) {
        // Empty / NaN buffer — let three.js handle the degenerate case.
        geom.computeBoundingBox();
        geom.computeBoundingSphere();
        return;
    }

    const box = new THREE.Box3(
        new THREE.Vector3(minx, miny, minz),
        new THREE.Vector3(maxx, maxy, maxz),
    );
    geom.boundingBox = box;

    // Bounding sphere: centre = box centre, radius = max vertex distance
    // (identical to THREE's algorithm, so frustum culling is unchanged).
    const cx = (minx + maxx) * 0.5;
    const cy = (miny + maxy) * 0.5;
    const cz = (minz + maxz) * 0.5;
    let maxR2 = 0;
    for (let i = 0; i < n; i += 3) {
        const dx = arr[i] - cx, dy = arr[i + 1] - cy, dz = arr[i + 2] - cz;
        const r2 = dx * dx + dy * dy + dz * dz;
        if (r2 > maxR2) maxR2 = r2;
    }
    geom.boundingSphere = new THREE.Sphere(new THREE.Vector3(cx, cy, cz), Math.sqrt(maxR2));
}

/** Scene-space bounding box as the union of each mesh's (now-cheap)
 * geometry boundingBox transformed by its world matrix. Equivalent result
 * to ``new THREE.Box3().setFromObject(root)`` but relies on the bounds
 * fastComputeBounds already set, so it never iterates vertices. */
export function fastSceneBox(root: THREE.Object3D): THREE.Box3 {
    const out = new THREE.Box3();
    const tmp = new THREE.Box3();
    root.updateWorldMatrix(false, true);
    root.traverse((obj) => {
        const mesh = obj as THREE.Mesh;
        const geom = mesh.geometry as THREE.BufferGeometry | undefined;
        if (!geom || !(mesh as unknown as {isMesh?: boolean}).isMesh) return;
        fastComputeBounds(geom);
        if (!geom.boundingBox) return;
        tmp.copy(geom.boundingBox).applyMatrix4(mesh.matrixWorld);
        out.union(tmp);
    });
    return out;
}
