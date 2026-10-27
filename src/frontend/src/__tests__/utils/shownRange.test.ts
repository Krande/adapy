import assert from "node:assert/strict";
import { test } from "node:test";

import * as THREE from "three";

import type { FeaManifestField } from "../../services/viewerApi";
import { applyElemFieldToMesh, shownElemFieldRange } from "../../utils/scene/fea/applyElemField";
import { applyFieldToMesh } from "../../utils/scene/fea/applyField";
import { legendScopeFor } from "../../utils/scene/fea/caseSteps";
import { setResultLineSegmentsExcluded } from "../../utils/scene/fea/resultLineSegments";
import { elementShownPredicate, shownNodalRange } from "../../utils/scene/fea/shownRange";

// The automatic colour range is measured over what is drawn: this step's values on
// the elements that are not hidden. A model whose extremes sit in hidden elements
// (GeniE dummy masses) or in another step used to paint one colour.

/** Two triangles over four nodes: E1 = nodes 0,1,2 and E2 = nodes 1,3,2. */
function makeMesh(hidden: string[] = []): THREE.Mesh {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    "position",
    new THREE.BufferAttribute(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0]), 3),
  );
  geometry.setIndex([0, 1, 2, 1, 3, 2]);
  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
  const hiddenSet = new Set(hidden);
  Object.assign(mesh, {
    drawRanges: new Map<string, [number, number]>([
      ["E1", [0, 3]],
      ["E2", [3, 3]],
    ]),
    getHiddenRanges: () => hiddenSet,
  });
  return mesh;
}

function elemField(labels: number[], support = "element"): FeaManifestField {
  return {
    name_canonical: "sesam.elements.stress",
    name_native: "sesam.elements.stress",
    kind: "scalar",
    category: "stress",
    support,
    analysis_kind: "static",
    components: ["SIGXX"],
    per_type: [
      {
        elem_type: "triangle",
        n_elements: labels.length,
        n_ips: 1,
        ip_layout: [],
        element_labels: labels,
        blob: { url: "f.bin", header_bytes: 1024, stride_bytes: 4 * labels.length, dtype: "float32", byte_order: "little" },
        scalar_range: { SIGXX: [-1000, 1000] },
      },
    ],
    n_steps: 1,
    steps: [{ i: 0, value: 1, label: "1" }],
    scalar_range: { SIGXX: [-1000, 1000] },
    default_view: { reduction: "SIGXX", colormap: "viridis" },
  } as FeaManifestField;
}

test("nodal range covers only the nodes of drawn faces", () => {
  const scalars = new Float32Array([1, 2, 3, 500]);
  assert.deepEqual(shownNodalRange(scalars, { mesh: makeMesh(), nPoints: 4 }), [1, 500]);
  // Hiding E2 drops node 3, the only node outside E1.
  assert.deepEqual(shownNodalRange(scalars, { mesh: makeMesh(["E2"]), nPoints: 4 }), [1, 3]);
  assert.equal(shownNodalRange(scalars, { mesh: makeMesh(["E1", "E2"]), nPoints: 4 }), null);
});

test("nodal range takes the end nodes of drawn beam solids, only while they are on", () => {
  const scalars = new Float32Array([1, 2, 3, 500, -40]);
  const main = makeMesh(["E2"]);
  const solidGeom = new THREE.BufferGeometry();
  solidGeom.setAttribute("position", new THREE.BufferAttribute(new Float32Array(9), 3));
  solidGeom.setIndex([0, 1, 2]);
  const solids = new THREE.Mesh(solidGeom, new THREE.MeshBasicMaterial());
  Object.assign(solids, {
    drawRanges: new Map<string, [number, number]>([["E9", [0, 3]]]),
    getHiddenRanges: () => new Set<string>(),
  });
  // Every solid vertex lerps node 0 -> node 4.
  const warp = {
    n_verts: 3,
    node0: new Uint32Array([0, 0, 0]),
    node1: new Uint32Array([4, 4, 4]),
    t: new Float32Array([0, 0.5, 1]),
  };
  const surfaces = { mesh: main, nPoints: 5, beamSolidMesh: solids, beamSolidWarp: warp };
  assert.deepEqual(shownNodalRange(scalars, surfaces), [-40, 3]);
  solids.visible = false;
  assert.deepEqual(shownNodalRange(scalars, surfaces), [1, 3]);
});

test("element range skips hidden faces and excluded beam lines", () => {
  const mesh = makeMesh(["E2"]);
  // E3 is a beam: no face, drawn as a coloured line unless excluded.
  const field = elemField([1, 2, 3]);
  const values = [new Float32Array([10, 900, -20])];
  const range = () =>
    shownElemFieldRange({
      field,
      perTypeStepValues: values,
      layer: "all",
      ipReduction: "mean",
      reduction: "SIGXX",
      isShown: elementShownPredicate({ mesh, nPoints: 4 }),
    });
  assert.deepEqual(range(), [-20, 10]);
  setResultLineSegmentsExcluded(mesh, new Set(["E3"]));
  assert.deepEqual(range(), [10 - 1e-5, 10 + 1e-5]);
});

test("the painters colour against the shown range instead of the field's", () => {
  const field = elemField([1, 2]);
  const paint = (autoRange?: [number, number]) => {
    const mesh = makeMesh();
    applyElemFieldToMesh({
      mesh,
      basePositions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0]),
      colorField: field,
      perTypeStepValues: [new Float32Array([10, 20])],
      layer: "all",
      ipReduction: "mean",
      reduction: "SIGXX",
      colormap: "viridis",
      autoRange,
    });
    const colors = mesh.geometry.getAttribute("color").array as Float32Array;
    return [Array.from(colors.slice(0, 3)), Array.from(colors.slice(9, 12))];
  };
  // Against ±1000 both elements are near enough the same mid-scale colour ...
  const [a, b] = paint();
  assert.deepEqual(a.map((c) => c.toFixed(1)), b.map((c) => c.toFixed(1)));
  // ... against what is shown they are the two ends of it.
  const [lo, hi] = paint([10, 20]);
  assert.notDeepEqual(lo.map((c) => c.toFixed(2)), hi.map((c) => c.toFixed(2)));

  const nodalField = { ...field, per_type: undefined, components: ["U"], scalar_range: { U: [0, 100] } } as FeaManifestField;
  const mesh = makeMesh();
  applyFieldToMesh({
    mesh,
    basePositions: new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0]),
    colorField: nodalField,
    colorStepValues: new Float32Array([1, 1, 2, 2]),
    reduction: "U",
    colormap: "viridis",
    autoRange: [1, 2],
  });
  const colors = mesh.geometry.getAttribute("color").array as Float32Array;
  assert.notDeepEqual(Array.from(colors.slice(0, 3)), Array.from(colors.slice(9, 12)));
});

test("the legend names a range measured over what is shown, envelope still wins", () => {
  assert.deepEqual(
    legendScopeFor({ hasCombinations: true, isCase: true, envelopeMode: false, envelopeAvailable: false, fromShown: true }),
    { scope: "shown", label: "shown elements" },
  );
  assert.deepEqual(
    legendScopeFor({ hasCombinations: true, isCase: false, envelopeMode: true, envelopeAvailable: true, fromShown: true }),
    { scope: "envelope", label: "over all combinations" },
  );
});
