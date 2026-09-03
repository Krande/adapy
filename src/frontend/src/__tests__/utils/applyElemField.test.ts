import assert from "node:assert/strict";
import { test } from "node:test";

import * as THREE from "three";

import type { FeaManifestField } from "../../services/viewerApi";
import { applyElemFieldToMesh } from "../../utils/scene/fea/applyElemField";
import { applyFieldToMesh } from "../../utils/scene/fea/applyField";
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

function makeSharedMesh(): THREE.Mesh {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    "position",
    new THREE.BufferAttribute(
      new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0]),
      3,
    ),
  );
  geometry.setIndex([0, 1, 2, 1, 3, 2]);
  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
  Object.assign(mesh, {
    drawRanges: new Map([
      ["E7", [0, 3]],
      ["E8", [3, 3]],
    ]),
  });
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

test("a single physical result layer does not gain a synthetic all choice", () => {
  const field = makeField();
  field.per_type![0].ip_layout = [{ ip: 0, layer: "mid", in_plane: "0" }];

  assert.deepEqual(availableResultLayers(field), ["mid"]);
});

test("multiple integration-point layers retain the all reduction choice", () => {
  const field = makeField();
  field.per_type![0].ip_layout = [
    { ip: 0, layer: "upper", in_plane: "0" },
    { ip: 1, layer: "lower", in_plane: "0" },
  ];

  assert.deepEqual(availableResultLayers(field), ["upper", "lower", "all"]);
});

test("flat element fields duplicate shared vertices to preserve discontinuities", () => {
  const mesh = makeSharedMesh();
  const field = makeField();
  field.support = "element_average";
  field.per_type![0].n_elements = 2;
  field.per_type![0].n_ips = 1;
  field.per_type![0].element_labels = [7, 8];
  field.scalar_range = { SIGXX: [0, 10] };
  applyElemFieldToMesh({
    mesh,
    basePositions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0]),
    colorField: field,
    perTypeStepValues: [new Float32Array([0, 10])],
    layer: "all",
    ipReduction: "mean",
    reduction: "SIGXX",
    colormap: "viridis",
  });

  assert.equal(mesh.geometry.getAttribute("position").count, 6);
  const colors = mesh.geometry.getAttribute("color").array as Float32Array;
  assert.deepEqual(Array.from(colors.slice(0, 3)), Array.from(colors.slice(6, 9)));
  assert.deepEqual(Array.from(colors.slice(9, 12)), Array.from(colors.slice(15, 18)));
  assert.notDeepEqual(Array.from(colors.slice(3, 6)), Array.from(colors.slice(9, 12)));
});

test("nodal fields still map correctly after element-local vertex expansion", () => {
  const mesh = makeSharedMesh();
  const elementField = makeField();
  elementField.support = "element_average";
  elementField.per_type![0].n_elements = 2;
  elementField.per_type![0].n_ips = 1;
  elementField.per_type![0].element_labels = [7, 8];
  const sourcePositions = new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0]);
  applyElemFieldToMesh({
    mesh,
    basePositions: sourcePositions,
    colorField: elementField,
    perTypeStepValues: [new Float32Array([0, 2])],
    layer: "all",
    ipReduction: "mean",
    reduction: "SIGXX",
  });

  const nodalField = makeField();
  nodalField.support = "nodal";
  nodalField.per_type = undefined;
  nodalField.scalar_range = { SIGXX: [0, 3] };
  applyFieldToMesh({
    mesh,
    basePositions: sourcePositions,
    colorField: nodalField,
    colorStepValues: new Float32Array([0, 1, 2, 3]),
    reduction: "SIGXX",
  });

  const colors = mesh.geometry.getAttribute("color").array as Float32Array;
  assert.deepEqual(Array.from(colors.slice(3, 6)), Array.from(colors.slice(9, 12)));
  assert.deepEqual(Array.from(colors.slice(6, 9)), Array.from(colors.slice(15, 18)));
});

test("result-point fields create colored markers at corners and centroid", () => {
  const mesh = makeMesh();
  const field = makeField();
  field.support = "result_point";
  field.per_type![0].n_ips = 4;
  field.per_type![0].ip_layout = [
    { ip: 0, layer: "top", in_plane: "0", node_index: 0 },
    { ip: 1, layer: "top", in_plane: "1", node_index: 1 },
    { ip: 2, layer: "top", in_plane: "(0.5, 0.5)", natural_coordinates: [0.5, 0.5] },
    { ip: 3, layer: "top", in_plane: "2", node_index: 2 },
  ];
  applyElemFieldToMesh({
    mesh,
    basePositions,
    colorField: field,
    perTypeStepValues: [new Float32Array([0, 1, 2, 3])],
    layer: "top",
    ipReduction: "mean",
    reduction: "SIGXX",
  });

  const markers = mesh.getObjectByName("__fea_result_point_markers__") as THREE.Points;
  assert.ok(markers);
  const positions = markers.geometry.getAttribute("position").array as Float32Array;
  assert.equal(markers.geometry.getAttribute("position").count, 4);
  assert.deepEqual(Array.from(positions.slice(0, 6)), [0, 0, 0, 1, 0, 0]);
  assert.deepEqual(
    Array.from(positions.slice(6, 9)),
    [1 / 3, 1 / 3, 0].map((value) => Math.fround(value)),
  );
});

test("beam result-point markers use source connectivity without triangle ranges", () => {
  const mesh = makeMesh();
  Object.assign(mesh, { drawRanges: new Map() });
  const field = makeField();
  field.support = "line_result_point";
  field.per_type![0].n_ips = 3;
  field.per_type![0].element_node_indices = [[0, 1]];
  field.per_type![0].ip_layout = [
    { ip: 0, layer: "mid", in_plane: "0", natural_coordinates: [0] },
    { ip: 1, layer: "mid", in_plane: "0.5", natural_coordinates: [0.5] },
    { ip: 2, layer: "mid", in_plane: "1", natural_coordinates: [1] },
  ];
  applyElemFieldToMesh({
    mesh,
    basePositions,
    colorField: field,
    perTypeStepValues: [new Float32Array([0, 1, 2])],
    layer: "mid",
    ipReduction: "mean",
    reduction: "SIGXX",
  });

  const markers = mesh.getObjectByName("__fea_result_point_markers__") as THREE.Points;
  assert.ok(markers);
  assert.deepEqual(
    Array.from(markers.geometry.getAttribute("position").array as Float32Array),
    [0, 0, 0, 0.5, 0, 0, 1, 0, 0],
  );
});

test("beam element fields color a line fallback without beam solids", () => {
  const mesh = makeMesh();
  Object.assign(mesh, { drawRanges: new Map() });
  const field = makeField();
  field.per_type![0].elem_type = "line";
  field.per_type![0].n_ips = 2;
  field.per_type![0].element_node_indices = [[0, 1]];
  field.per_type![0].ip_layout = [
    { ip: 0, layer: "mid", in_plane: "0", natural_coordinates: [0] },
    { ip: 1, layer: "mid", in_plane: "1", natural_coordinates: [1] },
  ];
  applyElemFieldToMesh({
    mesh,
    basePositions,
    colorField: field,
    perTypeStepValues: [new Float32Array([0, 2])],
    layer: "mid",
    ipReduction: "mean",
    reduction: "SIGXX",
  });

  const lines = mesh.getObjectByName("__fea_result_line_segments__") as THREE.LineSegments;
  assert.ok(lines);
  assert.deepEqual(
    Array.from(lines.geometry.getAttribute("position").array as Float32Array),
    [0, 0, 0, 1, 0, 0],
  );
  const colors = lines.geometry.getAttribute("color").array as Float32Array;
  assert.notDeepEqual(Array.from(colors.slice(0, 3)), Array.from(colors.slice(3, 6)));

  const nodalField = makeField();
  nodalField.support = "nodal";
  delete nodalField.per_type;
  applyFieldToMesh({
    mesh,
    basePositions,
    colorField: nodalField,
    colorStepValues: new Float32Array([0, 1, 2]),
    reduction: "SIGXX",
  });
  assert.equal(mesh.getObjectByName("__fea_result_line_segments__"), undefined);
});
