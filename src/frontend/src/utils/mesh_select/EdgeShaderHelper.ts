// utils/mesh_select/EdgeShader.ts
import * as THREE from 'three';
import { mergeGeometries } from 'three/examples/jsm/utils/BufferGeometryUtils.js';
import { useOptionsStore } from '@/state/optionsStore';

/**
 * Build one big LineSegments geometry where each vertex gets a 'rangeId' attribute.
 */
export function buildEdgeGeometryWithRangeIds(
  baseGeo: THREE.BufferGeometry,
  drawRanges: Map<string,[number,number]>
): { geometry: THREE.BufferGeometry; rangeIdToIndex: Map<string,number> } {
  const perRange: THREE.BufferGeometry[] = [];
  const rangeIdToIndex = new Map<string,number>();
  let idx = 0;
  const posAttr = baseGeo.attributes.position as THREE.BufferAttribute;

  // ``EdgesGeometry``'s ``thresholdAngle`` controls which adjacent
  // triangles emit a shared edge. The Three.js default of 1° emits
  // an edge for every tessellated triangle pair on a curved surface,
  // so a cylindrical or spherical face shows its full triangulation
  // grid. 30° keeps real feature edges (corners, silhouettes) while
  // dropping the near-coplanar tessellation lines. Smaller edge
  // geometry also means fewer line-segments rendered each frame —
  // strictly a perf win, no shader changes.
  const hideTess = useOptionsStore.getState().hideTessellationEdges;
  const thresholdAngle = hideTess ? 30 : 1;

  drawRanges.forEach(([start,count], rangeId) => {
    const slice = (baseGeo.index!.array as Uint16Array|Uint32Array)
      .slice(start, start+count);
    const sub = new THREE.BufferGeometry();
    sub.setAttribute('position', posAttr);
    sub.setIndex(Array.from(slice));

    // EdgesGeometry is already non-indexed, so we can skip toNonIndexed()
    const edges = new THREE.EdgesGeometry(sub, thresholdAngle);

    // attach a constant rangeId per-vertex
    const verts = edges.attributes.position.count;
    const idArr = new Float32Array(verts).fill(idx);
    edges.setAttribute('rangeId', new THREE.BufferAttribute(idArr, 1));

    perRange.push(edges);
    rangeIdToIndex.set(rangeId, idx++);
  });

  const merged = mergeGeometries(perRange, false)!;
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
  const vs = `
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
    precision mediump float;
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
