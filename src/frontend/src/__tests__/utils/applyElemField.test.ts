import assert from "node:assert/strict";
import { test } from "node:test";

import * as THREE from "three";

import type { FeaManifestField } from "../../services/viewerApi";
import { applyElemFieldToMesh } from "../../utils/scene/fea/applyElemField";
import { availableResultLayers } from "../../utils/scene/fea/resultLayers";

const basePositions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]);

function makeMesh(): THREE.Mesh {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    "position",
    new THREE.BufferAttribute(basePositions.slice(), 3),
  );
  geometry.setIndex([0, 1, 2]);
  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
  Object.assign(mesh, { drawRanges: new Map([["E7", [0, 3]]]) });
  return mesh;
}

function makeField(): FeaManifestField {
  return {
    name_canonical: "sesam.elements.g_stress",
    name_native: "sesam.elements.g_stress",
    kind: "scalar",
    category: "stress",
    support: "element_nodal",
    analysis_kind: "static",
    components: ["SIGXX"],
    per_type: [
      {
        elem_type: "triangle",
        n_elements: 1,
        n_ips: 3,
        ip_layout: [],
        element_labels: [7],
        blob: {
          url: "field.bin",
          header_bytes: 1024,
          stride_bytes: 12,
          dtype: "float32",
          byte_order: "little",
        },
        scalar_range: { SIGXX: [0, 2] },
      },
    ],
    n_steps: 1,
    steps: [{ i: 0, value: 1, label: "1" }],
    scalar_range: { SIGXX: [0, 2] },
    default_view: { reduction: "SIGXX", colormap: "viridis" },
  };
}

test("element-nodal support applies one scalar per source corner", () => {
  const mesh = makeMesh();
  applyElemFieldToMesh({
    mesh,
    basePositions,
    colorField: makeField(),
    perTypeStepValues: [new Float32Array([0, 1, 2])],
    layer: "all",
    ipReduction: "mean",
    reduction: "SIGXX",
    colormap: "viridis",
  });

  const colors = mesh.geometry.getAttribute("color").array as Float32Array;
  assert.notDeepEqual(
    Array.from(colors.slice(0, 3)),
    Array.from(colors.slice(3, 6)),
  );
  assert.notDeepEqual(
    Array.from(colors.slice(3, 6)),
    Array.from(colors.slice(6, 9)),
  );
});

test("inapplicable NaN element values retain the neutral color", () => {
  const mesh = makeMesh();
  applyElemFieldToMesh({
    mesh,
    basePositions,
    colorField: makeField(),
    perTypeStepValues: [new Float32Array([NaN, NaN, NaN])],
    layer: "all",
    ipReduction: "mean",
    reduction: "SIGXX",
    colormap: "viridis",
  });

  const colors = mesh.geometry.getAttribute("color").array as Float32Array;
  assert.deepEqual(Array.from(colors), new Array(9).fill(0.5));
});

test("element-nodal support applies only the selected shell surface", () => {
  const mesh = makeMesh();
  const field = makeField();
  field.surface = "selectable";
  field.per_type![0].n_ips = 6;
  field.per_type![0].ip_layout = [
    { ip: 0, layer: "top", in_plane: "1" },
    { ip: 1, layer: "top", in_plane: "2" },
    { ip: 2, layer: "top", in_plane: "3" },
    { ip: 3, layer: "bottom", in_plane: "1" },
    { ip: 4, layer: "bottom", in_plane: "2" },
    { ip: 5, layer: "bottom", in_plane: "3" },
  ];

  assert.deepEqual(availableResultLayers(field), ["top", "bottom"]);
  applyElemFieldToMesh({
    mesh,
    basePositions,
    colorField: field,
    perTypeStepValues: [new Float32Array([0, 1, 2, 2, 1, 0])],
    layer: "bottom",
    ipReduction: "mean",
    reduction: "SIGXX",
    colormap: "viridis",
  });

  const colors = mesh.geometry.getAttribute("color").array as Float32Array;
  assert.notDeepEqual(
    Array.from(colors.slice(0, 3)),
    Array.from(colors.slice(3, 6)),
  );
  assert.notDeepEqual(
    Array.from(colors.slice(3, 6)),
    Array.from(colors.slice(6, 9)),
  );
  // Bottom values are reversed relative to the top fixture, so first and last
  // source corners must receive different colours in that same order.
  assert.notDeepEqual(
    Array.from(colors.slice(0, 3)),
    Array.from(colors.slice(6, 9)),
  );
});
