import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import * as THREE from "three";

import type { FeaManifest, FeaManifestField } from "../../services/viewerApi";
import { useColorStore } from "../../state/colorLegendStore";
import { useFeaAnimationStore } from "../../state/feaAnimationStore";
import { DEFAULT_CONTOUR } from "../../utils/scene/fea/contourScale";
import { syncResultSession } from "../../utils/scene/fea/streaming/legendSync";

// What the controls and the legend are told after a paint, given the stores
// as they stand. No mesh is fetched or painted here: the mesh is a triangle,
// the manifest is one field, and what is pinned is the bookkeeping -- session
// on or off, range by analysis kind, legend from the field's range through
// the contour resolver, and the slider written only when the caller moved it.

function makeMesh(): THREE.Mesh {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute(
    "position",
    new THREE.BufferAttribute(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]), 3),
  );
  const mesh = new THREE.Mesh(geometry, new THREE.MeshBasicMaterial());
  mesh.morphTargetInfluences = [1];
  return mesh;
}

function makeField(analysis_kind: "static" | "eigen" = "static"): FeaManifestField {
  return {
    name_canonical: "displacement",
    name_native: "U",
    kind: "vector",
    category: "displacement",
    support: "nodal",
    analysis_kind,
    components: ["X", "Y", "Z"],
    n_steps: 3,
    steps: [
      { i: 0, value: 0, label: "0" },
      { i: 1, value: 1, label: "1" },
      { i: 2, value: 2, label: "2" },
    ],
    scalar_range: { magnitude: [0, 0.02], X: [-0.01, 0.01] },
    default_view: { reduction: "magnitude", colormap: "viridis" },
  } as FeaManifestField;
}

function makeManifest(field: FeaManifestField | null): FeaManifest {
  return {
    version: 1,
    src: "deck.fem",
    mesh: { url: "mesh.glb", n_points: 3, n_cells: 1 },
    fields: field ? [field] : [],
  } as unknown as FeaManifest;
}

function sync(field: FeaManifestField | null, extra: Partial<Parameters<typeof syncResultSession>[0]> = {}) {
  const mesh = makeMesh();
  syncResultSession({
    mesh,
    sourceName: "deck.fem",
    manifest: makeManifest(field),
    field,
    fieldName: field ? field.name_canonical : null,
    reduction: field ? "magnitude" : null,
    colormap: "viridis",
    stepIndex: field ? 2 : 0,
    sliderFactor: undefined,
    ...extra,
  });
  return mesh;
}

beforeEach(() => {
  useFeaAnimationStore.getState().reset();
  useFeaAnimationStore.setState({ factor: 0.3, contour: DEFAULT_CONTOUR });
  useColorStore.setState({ min: 5, max: 50, showLegend: false });
});

test("a painted field activates the session and drives the legend off its range", () => {
  const mesh = sync(makeField());
  const fea = useFeaAnimationStore.getState();
  assert.equal(fea.sessionActive, true);
  assert.equal(fea.mesh, mesh);
  assert.equal(fea.sourceName, "deck.fem");
  assert.deepEqual(fea.range, [0, 1]);
  assert.equal(fea.nSteps, 3);
  assert.equal(fea.stepIndex, 2);
  assert.equal(fea.fieldName, "displacement");
  assert.equal(fea.reduction, "magnitude");
  assert.equal(fea.colormap, "viridis");
  const legend = useColorStore.getState();
  assert.equal(legend.showLegend, true);
  assert.equal(legend.min, 0);
  assert.equal(legend.max, 0.02);
});

test("an eigen field sweeps both ways", () => {
  sync(makeField("eigen"));
  assert.deepEqual(useFeaAnimationStore.getState().range, [-1, 1]);
});

test("a pinned contour bound reaches the legend, as it reached the paint", () => {
  useFeaAnimationStore.setState({ contour: { levels: null, min: 0.005, max: null } });
  sync(makeField());
  const legend = useColorStore.getState();
  assert.equal(legend.min, 0.005);
  assert.equal(legend.max, 0.02);
});

test("the slider is left alone unless the caller moved it", () => {
  useFeaAnimationStore.setState({ scaleFactor: 50, scaleFactorAuto: false });
  const untouched = sync(makeField());
  assert.equal(useFeaAnimationStore.getState().factor, 0.3);
  assert.equal(untouched.morphTargetInfluences![0], 1);

  const moved = sync(makeField(), { sliderFactor: 0.5 });
  assert.equal(useFeaAnimationStore.getState().factor, 0.5);
  // Slider times the scale on screen: the influence the controls now describe.
  assert.equal(moved.morphTargetInfluences![0], 25);
});

test("a field-less mesh is no result session and shows no legend", () => {
  useColorStore.setState({ showLegend: true });
  sync(null);
  const fea = useFeaAnimationStore.getState();
  assert.equal(fea.sessionActive, false);
  assert.equal(fea.fieldName, null);
  assert.equal(fea.nSteps, 1);
  assert.equal(fea.stepIndex, 0);
  assert.equal(useColorStore.getState().showLegend, false);
});
