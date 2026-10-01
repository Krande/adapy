// The edge overlay's geometry, as plain typed arrays in and out.
//
// No three.js and no store, so the SAME function runs on the main thread (a live rebuild after an
// options toggle, `buildEdgeGeometryWithRangeIds`) and in the edge worker (every model load,
// `buildEdgeGeometryAsync`): one implementation, two places to run it.
//
// Same semantics as per-range ``THREE.EdgesGeometry``: boundary edges plus edges whose dihedral
// angle exceeds the threshold, with vertices welded by position first because the tessellated GLB
// is a triangle soup (every triangle owns unique sequential indices).

export interface EdgeArrays {
  /** Segment endpoints, xyz per vertex, two vertices per segment. */
  positions: Float32Array;
  /** Per output vertex: the index (into the ranges as passed) of the draw range it came from. */
  rangeIdx: Float32Array;
}

/**
 * @param pos        the mesh's position attribute
 * @param index      its index
 * @param ranges     `[start, count]` index ranges, one per draw range; an output vertex's
 *                   `rangeIdx` is its range's position in this list
 * @param thresholdDot emit a shared edge when dot(n1, n2) <= this (cos of the threshold angle)
 */
export function computeEdgeArrays(
  pos: Float32Array,
  index: Uint16Array | Uint32Array,
  ranges: readonly (readonly [number, number])[],
  thresholdDot: number,
): EdgeArrays {
  // Output accumulators: one position chunk per range (chunks are exact-sized,
  // concatenated once at the end — no growable-array churn).
  const posChunks: Float32Array[] = [];
  const chunkRangeIdx: number[] = [];
  let totalVerts = 0;

  // Reusable per-range scratch. Vertices are first welded per range via a quantised spatial hash
  // (1e-4 model units, matching EdgesGeometry's 4-digit precision); edge keys then pack the welded
  // (lo, hi) pair into one float-safe integer: lo * 2^26 + hi stays exact below 2^52 for meshes up
  // to 67M vertices. The edge map stores the first face's index (+1, so 0 means "consumed");
  // boundary edges remain and are flushed at the end.
  const weldMap = new Map<number, number>();
  const edgeMap = new Map<number, number>();
  const KEY_SHIFT = 1 << 26;
  const INV_TOL = 1e4;

  const weld = (v: number): number => {
    const o = v * 3;
    // Spatial hash; sums stay well below 2^53 for coordinates within ~1e5 units.
    const h =
      Math.round(pos[o] * INV_TOL) * 73856093 +
      Math.round(pos[o + 1] * INV_TOL) * 19349663 +
      Math.round(pos[o + 2] * INV_TOL) * 83492791;
    const found = weldMap.get(h);
    if (found !== undefined) return found;
    weldMap.set(h, v);
    return v;
  };

  ranges.forEach(([start, count], rangeIdx) => {
    const triCount = (count / 3) | 0;
    if (triCount === 0) return;

    // Pass 1: per-face normals for the dihedral test.
    const normals = new Float32Array(triCount * 3);
    for (let t = 0; t < triCount; t++) {
      const o = start + t * 3;
      const a = index[o] * 3, b = index[o + 1] * 3, c = index[o + 2] * 3;
      const abx = pos[b] - pos[a], aby = pos[b + 1] - pos[a + 1], abz = pos[b + 2] - pos[a + 2];
      const acx = pos[c] - pos[a], acy = pos[c + 1] - pos[a + 1], acz = pos[c + 2] - pos[a + 2];
      let nx = aby * acz - abz * acy, ny = abz * acx - abx * acz, nz = abx * acy - aby * acx;
      const len = Math.sqrt(nx * nx + ny * ny + nz * nz);
      if (len > 1e-20) { nx /= len; ny /= len; nz /= len; }
      const no = t * 3;
      normals[no] = nx; normals[no + 1] = ny; normals[no + 2] = nz;
    }

    // Pass 2: dedupe shared edges; decide each pair as soon as its second face
    // arrives. Collect emitted segment endpoints as vertex-index pairs.
    weldMap.clear();
    edgeMap.clear();
    const emitted: number[] = [];
    for (let t = 0; t < triCount; t++) {
      const o = start + t * 3;
      for (let e = 0; e < 3; e++) {
        const v0 = weld(index[o + e]);
        const v1 = weld(index[o + ((e + 1) % 3)]);
        if (v0 === v1) continue; // degenerate edge after welding
        const lo = v0 < v1 ? v0 : v1;
        const hi = v0 < v1 ? v1 : v0;
        const key = lo * KEY_SHIFT + hi;
        const prev = edgeMap.get(key);
        if (prev === undefined) {
          edgeMap.set(key, t + 1);
        } else if (prev > 0) {
          const p = (prev - 1) * 3, q = t * 3;
          const dot = normals[p] * normals[q] + normals[p + 1] * normals[q + 1] + normals[p + 2] * normals[q + 2];
          if (dot <= thresholdDot) emitted.push(lo, hi);
          edgeMap.set(key, 0); // consumed (3+-manifold repeats are ignored, as in EdgesGeometry)
        }
      }
    }
    // Boundary edges: seen exactly once.
    edgeMap.forEach((v, key) => {
      if (v > 0) emitted.push((key / KEY_SHIFT) | 0, key % KEY_SHIFT);
    });

    if (emitted.length === 0) return;
    const chunk = new Float32Array(emitted.length * 3);
    for (let i = 0; i < emitted.length; i++) {
      const v = emitted[i] * 3, w = i * 3;
      chunk[w] = pos[v]; chunk[w + 1] = pos[v + 1]; chunk[w + 2] = pos[v + 2];
    }
    posChunks.push(chunk);
    chunkRangeIdx.push(rangeIdx);
    totalVerts += emitted.length;
  });

  const positions = new Float32Array(totalVerts * 3);
  const rangeIdx = new Float32Array(totalVerts);
  let off = 0;
  for (let i = 0; i < posChunks.length; i++) {
    positions.set(posChunks[i], off * 3);
    rangeIdx.fill(chunkRangeIdx[i], off, off + posChunks[i].length / 3);
    off += posChunks[i].length / 3;
  }
  return { positions, rangeIdx };
}
