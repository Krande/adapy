import assert from "node:assert/strict";
import { test } from "node:test";

import * as THREE from "three";

import type { FeaManifestField } from "../../services/viewerApi";
import { applyElemFieldToMesh } from "../../utils/scene/fea/applyElemField";
import { ensureElementLocalVertices } from "../../utils/scene/fea/elementLocalGeometry";
import { writeMorphTarget } from "../../utils/scene/fea/gpuRefresh";
import { installBeamSolidWarp } from "../../utils/scene/fea/streaming/warp";

// A repaint that changes only the colour keeps the beam solids' warp. Rewriting an
// identical morph made three.js rebuild the solid's morph texture, a pass over every
// render vertex, on each field or component change and on the beams-as-solid toggle.

// One beam from node 0 to node 1, a single triangle of three solid vertices.
const nodePositions = new Float32Array([0, 0, 0, 1, 0, 0]);
const solidBase = new Float32Array([0, 0, 0, 1, 0, 0, 0.5, 0.1, 0]);
const warp = {
  n_verts: 3,
  node0: new Uint32Array([0, 0, 0]),
  node1: new Uint32Array([1, 1, 1]),
  t: new Float32Array([0, 1, 0.5]),
};
const displacement = {
  name_canonical: "displacement",
  name_native: "displacement",
  kind: "vector3",
  category: "displacement",
  support: "node",
  analysis_kind: "static",
  components: ["X", "Y", "Z"],
  n_steps: 1,
  steps: [{ i: 0, value: 1, label: "1" }],
  scalar_range: {},
  default_view: { reduction: "magnitude" },
} as unknown as FeaManifestField;

function setup(): { main: THREE.Mesh; solids: THREE.Mesh } {
  const main = new THREE.Mesh(new THREE.BufferGeometry(), new THREE.MeshBasicMaterial());
  main.morphTargetInfluences = [1];
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(solidBase.slice(), 3));
  geometry.setIndex([0, 1, 2]);
  const solids = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
  return { main, solids };
}

const morphOf = (mesh: THREE.Mesh) => mesh.geometry.morphAttributes.position[0] as THREE.BufferAttribute;

test("the same warp installed twice writes the morph once", () => {
  const { main, solids } = setup();
  const step = new Float32Array([0, 0, 0, 0, 0.2, 0]);
  installBeamSolidWarp(main, solids, solidBase, warp, displacement, step, nodePositions);
  const version = morphOf(solids).version;
  installBeamSolidWarp(main, solids, solidBase, warp, displacement, step, nodePositions);
  assert.equal(morphOf(solids).version, version);
});

test("another step rewrites it", () => {
  const { main, solids } = setup();
  installBeamSolidWarp(main, solids, solidBase, warp, displacement, new Float32Array(6), nodePositions);
  const step = new Float32Array([0, 0, 0, 0, 0.2, 0]);
  installBeamSolidWarp(main, solids, solidBase, warp, displacement, step, nodePositions);
  assert.ok(Math.abs(morphOf(solids).getY(1) - 0.2) < 1e-6);
});

test("a morph written by someone else in between is replaced", () => {
  const { main, solids } = setup();
  const step = new Float32Array([0, 0, 0, 0, 0.2, 0]);
  installBeamSolidWarp(main, solids, solidBase, warp, displacement, step, nodePositions);
  // A recorded playback frame, say.
  writeMorphTarget(solids.geometry, new Float32Array(9).fill(5), null);
  installBeamSolidWarp(main, solids, solidBase, warp, displacement, step, nodePositions);
  assert.ok(Math.abs(morphOf(solids).getY(1) - 0.2) < 1e-6);
  assert.equal(morphOf(solids).getY(0), 0);
});

test("an element paint told not to write the morph leaves the installed warp alone", () => {
  const { main, solids } = setup();
  Object.assign(solids, { drawRanges: new Map([["E1", [0, 3]]]) });
  const field = {
    ...displacement,
    name_canonical: "stress",
    category: "stress",
    support: "element_average",
    components: ["S"],
    per_type: [{
      elem_type: "line", n_elements: 1, n_ips: 1, ip_layout: [], element_labels: [1],
      blob: { url: "x", header_bytes: 0, stride_bytes: 4, dtype: "float32", byte_order: "little" },
      scalar_range: {},
    }],
  } as unknown as FeaManifestField;
  const paint = () => applyElemFieldToMesh({
    mesh: solids, basePositions: solidBase, colorField: field, perTypeStepValues: [new Float32Array([1])],
    layer: "all", ipReduction: "mean", reduction: "S", displacementScale: 1, writeMorph: false,
  });
  const step = new Float32Array([0, 0, 0, 0, 0.2, 0]);
  paint();
  installBeamSolidWarp(main, solids, solidBase, warp, displacement, step, nodePositions);
  const version = morphOf(solids).version;
  paint();
  installBeamSolidWarp(main, solids, solidBase, warp, displacement, step, nodePositions);
  assert.equal(morphOf(solids).version, version);
  assert.ok(Math.abs(morphOf(solids).getY(1) - 0.2) < 1e-6);
});

test("an element-local expansion in between is written through", () => {
  const { main, solids } = setup();
  const step = new Float32Array([0, 0, 0, 0, 0.2, 0]);
  installBeamSolidWarp(main, solids, solidBase, warp, displacement, step, nodePositions);
  ensureElementLocalVertices(solids.geometry);
  installBeamSolidWarp(main, solids, solidBase, warp, displacement, step, nodePositions);
  assert.equal(morphOf(solids).count, solids.geometry.getAttribute("position").count);
  assert.ok(Math.abs(morphOf(solids).getY(1) - 0.2) < 1e-6);
});
