// utils/mesh_select/EdgeShader.ts
import * as THREE from 'three';
import { useOptionsStore } from '@/state/optionsStore';

// Above this many indices in one mesh, force the feature-edge threshold (30°)
// even when the user hasn't enabled "hide tessellation edges": at 1° a curved
// CAD surface emits a line pair for nearly EVERY triangle edge, so a ~30M-tri
// merged mesh would produce a multi-GB line geometry that takes minutes to
// build and crawls at render time.
const FORCE_FEATURE_EDGES_INDEX_COUNT = 5_000_000;

/**
 * Build one big LineSegments geometry where each vertex gets a 'rangeId' attribute.
 *
 * Single typed-array pass with the same semantics as per-range
 * ``THREE.EdgesGeometry`` (boundary edges + edges whose dihedral angle exceeds
 * the threshold). The previous implementation built a THREE sub-geometry per
 * draw range — ``Array.from`` boxing every index into JS numbers, a full
 * ``EdgesGeometry`` per range, and a final ``mergeGeometries`` over tens of
 * thousands of geometries — which on big merged CAD meshes cost minutes of CPU
 * and GBs of transient allocations.
 */
export function buildEdgeGeometryWithRangeIds(
  baseGeo: THREE.BufferGeometry,
  drawRanges: Map<string,[number,number]>
): { geometry: THREE.BufferGeometry; rangeIdToIndex: Map<string,number> } {
  const rangeIdToIndex = new Map<string,number>();
  const posAttr = baseGeo.attributes.position as THREE.BufferAttribute;
  const pos = posAttr.array as Float32Array;
  const index = baseGeo.index!.array as Uint16Array | Uint32Array;

  const hideTess = useOptionsStore.getState().hideTessellationEdges;
  const forceFeature = index.length > FORCE_FEATURE_EDGES_INDEX_COUNT;
  if (forceFeature && !hideTess) {
    console.info(
      `buildEdgeGeometryWithRangeIds: ${index.length} indices > ` +
      `${FORCE_FEATURE_EDGES_INDEX_COUNT} — forcing 30° feature-edge threshold`,
    );
  }
  const thresholdAngle = hideTess || forceFeature ? 30 : 1;
  // EdgesGeometry's test: emit when dot(n1, n2) <= cos(threshold).
  const thresholdDot = Math.cos(THREE.MathUtils.DEG2RAD * thresholdAngle);

  // Output accumulators: one position chunk per range (chunks are exact-sized,
  // concatenated once at the end — no growable-array churn).
  const posChunks: Float32Array[] = [];
  const chunkRangeIdx: number[] = [];
  let totalVerts = 0;

  // Reusable per-range scratch. The tessellated GLB is a triangle soup (every
  // triangle owns unique sequential indices), so edge sharing must be detected
  // by POSITION, exactly like THREE.EdgesGeometry's hash. Vertices are first
  // welded per range via a quantised spatial hash (1e-4 model units, matching
  // EdgesGeometry's 4-digit precision); edge keys then pack the welded (lo, hi)
  // pair into one float-safe integer: lo * 2^26 + hi stays exact below 2^52 for
  // meshes up to 67M vertices. The edge map stores the first face's index (+1,
  // so 0 means "consumed"); boundary edges remain and are flushed at the end.
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

  drawRanges.forEach(([start, count], rangeId) => {
    const rangeIdx = rangeIdToIndex.size;
    rangeIdToIndex.set(rangeId, rangeIdx);
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

  const outPos = new Float32Array(totalVerts * 3);
  const outRange = new Float32Array(totalVerts);
  let off = 0;
  for (let i = 0; i < posChunks.length; i++) {
    outPos.set(posChunks[i], off * 3);
    outRange.fill(chunkRangeIdx[i], off, off + posChunks[i].length / 3);
    off += posChunks[i].length / 3;
  }

  const merged = new THREE.BufferGeometry();
  merged.setAttribute('position', new THREE.BufferAttribute(outPos, 3));
  merged.setAttribute('rangeId', new THREE.BufferAttribute(outRange, 1));
  return { geometry: merged, rangeIdToIndex };
}

/**
 * Create a ShaderMaterial that samples a small DataTexture of visibility flags
 * (so we never hit the "too many uniforms" limit) and one highlighted index.
 */
export function makeEdgeShaderMaterial(
  renderer: THREE.WebGLRenderer,
  numRanges: number
): THREE.ShaderMaterial {
  const maxTex = renderer.capabilities.maxTextureSize;
  const w = Math.min(numRanges, maxTex);
  const h = Math.ceil(numRanges / w);

  // 1 byte per range: 255==visible, 0==hidden
  const data = new Uint8Array(w*h).fill(255);
  const tex = new THREE.DataTexture(data, w, h, THREE.RedFormat, THREE.UnsignedByteType);
  tex.minFilter = THREE.NearestFilter;
  tex.magFilter = THREE.NearestFilter;
  tex.wrapS = THREE.ClampToEdgeWrapping;
  tex.wrapT = THREE.ClampToEdgeWrapping;
  tex.needsUpdate = true;

  const uniforms = {
    uVisibleTex: { value: tex },
    uTexSize:    { value: new THREE.Vector2(w,h) },
    uHighlighted:{ value: -1 }
  };

  // Clipping chunks let section planes cut the edge overlay too (needs
  // `clipping: true` on the material + a per-frame mvPosition for vClipPosition).
  // highp int is REQUIRED here, not cosmetic. rangeId/uHighlighted are
  // per-object indices that reach the full draw-range count (tens of
  // thousands on big models). In a WebGL1 fragment shader `int` defaults to
  // mediump, which GLSL ES only guarantees to ±2^10 (1024). On GPUs that
  // honour that minimum, any object with index > 1024 gets a saturated/
  // wrapped `rid` — so `rid == uHighlighted` mis-matches (a neighbour lights
  // up) and `rid % int(uTexSize.x)` samples the wrong visibility texel (the
  // selected object's own edges vanish). Faces are unaffected because they
  // highlight via CPU material-index, not this shader. Forcing highp int (and
  // float, so the vRangeId varying stays exact) fixes it for all indices.
  const vs = `
    precision highp float;
    precision highp int;
    attribute float rangeId;
    varying float vRangeId;
    #include <clipping_planes_pars_vertex>
    void main() {
      vRangeId = rangeId;
      vec4 mvPosition = modelViewMatrix * vec4(position,1.0);
      gl_Position = projectionMatrix * mvPosition;
      #include <clipping_planes_vertex>
    }
  `;

  const fs = `
    precision highp float;
    precision highp int;
    varying float vRangeId;
    uniform sampler2D uVisibleTex;
    uniform vec2 uTexSize;
    uniform int uHighlighted;
    #include <clipping_planes_pars_fragment>
    void main(){
      #include <clipping_planes_fragment>
      int rid = int(vRangeId + 0.5);
      int x = rid % int(uTexSize.x);
      int y = rid / int(uTexSize.x);
      vec2 uv = (vec2(float(x),float(y)) + 0.5) / uTexSize;
      float vis = texture2D(uVisibleTex,uv).r;
      if(vis < 0.5) discard;
      if(rid == uHighlighted) gl_FragColor = vec4(0,0,1,1);
      else             gl_FragColor = vec4(0,0,0,1);
    }
  `;

  return new THREE.ShaderMaterial({ uniforms, vertexShader:vs, fragmentShader:fs, clipping: true });
}
