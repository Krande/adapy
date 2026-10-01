// utils/mesh_select/EdgeShader.ts
import * as Comlink from 'comlink';
import * as THREE from 'three';
import { useOptionsStore } from '@/state/optionsStore';

import type { EdgeBuildApi } from './edgeBuild.worker';
import { computeEdgeArrays } from './edgeCore';

// Above this many indices in one mesh, force the feature-edge threshold (30°)
// even when the user hasn't enabled "hide tessellation edges": at 1° a curved
// CAD surface emits a line pair for nearly EVERY triangle edge, so a ~30M-tri
// merged mesh would produce a multi-GB line geometry that takes minutes to
// build and crawls at render time.
const FORCE_FEATURE_EDGES_INDEX_COUNT = 5_000_000;

/** The threshold the overlay is built with: 30° (feature edges) when tessellation edges are
 *  hidden or the mesh is too large for anything finer, else 1°. Read on the main thread -- the
 *  worker has no store. */
function edgeThresholdDot(indexCount: number): number {
  const hideTess = useOptionsStore.getState().hideTessellationEdges;
  const forceFeature = indexCount > FORCE_FEATURE_EDGES_INDEX_COUNT;
  if (forceFeature && !hideTess) {
    console.info(
      `buildEdgeGeometryWithRangeIds: ${indexCount} indices > ` +
      `${FORCE_FEATURE_EDGES_INDEX_COUNT} — forcing 30° feature-edge threshold`,
    );
  }
  const thresholdAngle = hideTess || forceFeature ? 30 : 1;
  // EdgesGeometry's test: emit when dot(n1, n2) <= cos(threshold).
  return Math.cos(THREE.MathUtils.DEG2RAD * thresholdAngle);
}

/** Draw ranges in iteration order, with the id -> position map the edge shader indexes by. */
function rangeList(drawRanges: Map<string, [number, number]>) {
  const rangeIdToIndex = new Map<string, number>();
  const ranges: [number, number][] = [];
  drawRanges.forEach((range, rangeId) => {
    rangeIdToIndex.set(rangeId, ranges.length);
    ranges.push([range[0], range[1]]);
  });
  return { ranges, rangeIdToIndex };
}

function edgeGeometry(positions: Float32Array, rangeIdx: Float32Array): THREE.BufferGeometry {
  const merged = new THREE.BufferGeometry();
  merged.setAttribute('position', new THREE.BufferAttribute(positions, 3));
  merged.setAttribute('rangeId', new THREE.BufferAttribute(rangeIdx, 1));
  return merged;
}

/**
 * Build one big LineSegments geometry where each vertex gets a 'rangeId' attribute.
 *
 * Single typed-array pass (`computeEdgeArrays`) with the same semantics as per-range
 * ``THREE.EdgesGeometry``. The previous implementation built a THREE sub-geometry per
 * draw range — ``Array.from`` boxing every index into JS numbers, a full
 * ``EdgesGeometry`` per range, and a final ``mergeGeometries`` over tens of
 * thousands of geometries — which on big merged CAD meshes cost minutes of CPU
 * and GBs of transient allocations. On the main thread: what a live rebuild uses; a load
 * uses `buildEdgeGeometryAsync`.
 */
export function buildEdgeGeometryWithRangeIds(
  baseGeo: THREE.BufferGeometry,
  drawRanges: Map<string,[number,number]>
): { geometry: THREE.BufferGeometry; rangeIdToIndex: Map<string,number> } {
  const pos = (baseGeo.attributes.position as THREE.BufferAttribute).array as Float32Array;
  const index = baseGeo.index!.array as Uint16Array | Uint32Array;
  const { ranges, rangeIdToIndex } = rangeList(drawRanges);
  const out = computeEdgeArrays(pos, index, ranges, edgeThresholdDot(index.length));
  return { geometry: edgeGeometry(out.positions, out.rangeIdx), rangeIdToIndex };
}

// A small pool: two meshes' edges build side by side, and a burst of loads queues rather than
// spawning a worker per mesh. Created on first use, so a viewer that never shows edges pays nothing.
const EDGE_WORKERS = Math.max(1, Math.min(2, (globalThis.navigator?.hardwareConcurrency ?? 2) - 1));
let edgePool: Promise<Comlink.Remote<EdgeBuildApi>[]> | null = null;
let nextEdgeWorker = 0;

async function edgeWorker(): Promise<Comlink.Remote<EdgeBuildApi>> {
  // Imported on first use, not at module load: `?worker&inline` is Vite's (inlined into the bundle,
  // like the model cache worker), and a static import would make this module unloadable anywhere
  // else -- the unit tests import it for the synchronous builder.
  edgePool ??= import('./edgeBuild.worker.ts?worker&inline').then(({ default: EdgeBuildWorker }) =>
    Array.from({ length: EDGE_WORKERS }, () => Comlink.wrap<EdgeBuildApi>(new EdgeBuildWorker())),
  );
  const pool = await edgePool;
  return pool[nextEdgeWorker++ % pool.length];
}

/** `buildEdgeGeometryWithRangeIds`, computed in a worker: the same result, without the main thread
 *  stalling for the seconds a large merged mesh takes. The mesh's own arrays stay where they are
 *  -- the worker gets copies, since the mesh is drawn from the originals meanwhile. */
export async function buildEdgeGeometryAsync(
  baseGeo: THREE.BufferGeometry,
  drawRanges: Map<string,[number,number]>
): Promise<{ geometry: THREE.BufferGeometry; rangeIdToIndex: Map<string,number> }> {
  const pos = ((baseGeo.attributes.position as THREE.BufferAttribute).array as Float32Array).slice();
  const index = (baseGeo.index!.array as Uint16Array | Uint32Array).slice();
  const { ranges, rangeIdToIndex } = rangeList(drawRanges);
  const thresholdDot = edgeThresholdDot(index.length);
  const out = await (await edgeWorker()).build(
    Comlink.transfer(pos, [pos.buffer]),
    Comlink.transfer(index, [index.buffer]),
    ranges,
    thresholdDot,
  );
  return { geometry: edgeGeometry(out.positions, out.rangeIdx), rangeIdToIndex };
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
