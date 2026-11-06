// Isolating an FEA result's sets and property values against a real (headless) scene: the rest
// is hidden, each action is one undo entry, and undo / redo bring back the ranges, the isolation
// and the context choice together. Also the rows' selection, the commands and what a pick reads.

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";
import * as THREE from "three";

import { useFeaAnimationStore } from "@/state/feaAnimationStore";
import { useFeaIsolationStore } from "@/state/feaIsolationStore";
import { useObjectInfoStore } from "@/state/objectInfoStore";
import { useViewerHistoryStore } from "@/state/viewerHistoryStore";
import { createViewerRuntime, mountViewerRuntime } from "@/state/viewerRuntime";
import { CustomBatchedMesh } from "@/utils/mesh_select/CustomBatchedMesh";
import { redoViewerAction, undoViewerAction } from "@/utils/scene/viewerHistory";
import {
  clearFeaIsolation,
  hideFeaRows,
  isolateFeaRows,
  isolateFeaSetsMatching,
  showFeaRows,
  toggleFeaContextWireframe,
} from "@/utils/scene/fea/feaIsolation";
import { feaContextReason, feaIsolationReason, feaPickedInfo, feaTreeView } from "@/utils/scene/fea/feaTree";
import { elementSetRowId, nodeSetRowId, propertyValueRowId, type FeaSetInfo } from "@/utils/scene/fea/feaTreeRows";

await import("../fixtures/panelRealm");
const { registerCoreCommands } = await import("@/layout/coreCommands");
const { getCommand } = await import("@/layout/commands");
const { CORE_COMMAND_IDS: ID } = await import("@/layout/coreCommandIds");

function mesh(ids: string[]): CustomBatchedMesh {
  const pos: number[] = [];
  ids.forEach((_, i) => pos.push(i, 0, 0, i + 1, 0, 0, i + 1, 1, 0));
  const g = new THREE.BufferGeometry();
  g.setAttribute("position", new THREE.BufferAttribute(new Float32Array(pos), 3));
  g.setIndex(new THREE.BufferAttribute(new Uint32Array(pos.length / 3).map((_, i) => i), 1));
  const ranges = new Map<string, [number, number]>(ids.map((id, i) => [id, [i * 3, 3]]));
  return new CustomBatchedMesh(g, new THREE.MeshBasicMaterial(), ranges, "buf", true, null);
}

const SETS: FeaSetInfo[] = [
  { name: "Deck", members: ["EL1", "EL2"], fe_object_type: "element" },
  { name: "Side", members: ["EL2", "EL3"], fe_object_type: "element" },
  { name: "Corners", members: ["P1"], fe_object_type: "node" },
];

const MATERIAL = {
  name_canonical: "props.material",
  name_native: "MAT",
  kind: "scalar",
  category: "property",
  support: "element_average",
  analysis_kind: "static",
  components: ["value"],
  value_labels: { "1": "Steel", "2": "Alloy" },
  per_type: [{ elem_type: "quad", n_elements: 5, n_ips: 1, ip_layout: [], blob: {}, scalar_range: {} }],
  n_steps: 1,
  steps: [],
  scalar_range: {},
  default_view: { reduction: "scalar", colormap: "viridis" },
};

const runtime = createViewerRuntime();
mountViewerRuntime(runtime);
let m: CustomBatchedMesh;
const hidden = () => [...m.getHiddenRanges()].sort();
const hist = () => useViewerHistoryStore.getState();
const iso = () => useFeaIsolationStore.getState().isolation;

beforeEach(() => {
  const scene = new THREE.Scene();
  m = mesh(["E1", "E2", "E3", "E4", "E5"]);
  scene.add(m);
  (runtime.scene as { current: THREE.Scene | null }).current = scene;
  useFeaAnimationStore.setState({
    sourceName: "result.rmed",
    manifest: { version: 1, src: "x", mesh: { url: "m", n_points: 0, n_cells: 0, edges_url: "edges.bin" }, fields: [MATERIAL] } as never,
  });
  const s = useFeaIsolationStore.getState();
  s.reset("result.rmed");
  s.setSets("result.rmed", SETS);
  useFeaIsolationStore.setState({
    membership: new Map([["props.material", new Map([["E1", 1], ["E2", 1], ["E3", 2], ["E4", 2], ["E5", 1]])]]),
    isolation: { rows: [], contextWireframe: true },
  });
  hist().reset();
});

test("isolating a set hides the rest; undo and redo restore the ranges and the isolation", () => {
  isolateFeaRows([elementSetRowId("Deck")]);
  assert.deepEqual(hidden(), ["E3", "E4", "E5"]);
  assert.deepEqual(iso().rows, [elementSetRowId("Deck")]);
  assert.equal(hist().past.length, 1);
  assert.equal(hist().past[0].label, "isolate FEA sets (hide 3 objects)");

  undoViewerAction();
  assert.deepEqual(hidden(), []);
  assert.deepEqual(iso().rows, []);
  redoViewerAction();
  assert.deepEqual(hidden(), ["E3", "E4", "E5"]);
  assert.deepEqual(iso().rows, [elementSetRowId("Deck")]);
});

test("several rows isolate as a union, sets and property values alike", () => {
  isolateFeaRows([elementSetRowId("Side"), propertyValueRowId("props.material", 1)]);
  assert.deepEqual(hidden(), ["E4"]);
  isolateFeaRows([elementSetRowId("Side")], "remove");
  assert.deepEqual(hidden(), ["E3", "E4"]);
  isolateFeaRows([nodeSetRowId("Corners")], "add");
  assert.deepEqual(iso().rows, [propertyValueRowId("props.material", 1)], "a node set is not isolated");
});

test("clearing shows everything, as one entry; doing nothing records nothing", () => {
  clearFeaIsolation();
  assert.equal(hist().past.length, 0);
  isolateFeaRows([elementSetRowId("Deck")]);
  clearFeaIsolation();
  assert.deepEqual(hidden(), []);
  assert.equal(hist().past[1].label, "clear FEA isolation (show 3 objects)");
  undoViewerAction();
  assert.deepEqual(hidden(), ["E3", "E4", "E5"]);
  assert.deepEqual(iso().rows, [elementSetRowId("Deck")]);
});

test("the context toggle is an entry of its own and keeps the isolation", () => {
  isolateFeaRows([elementSetRowId("Deck")]);
  toggleFeaContextWireframe();
  assert.equal(iso().contextWireframe, false);
  assert.deepEqual(hidden(), ["E3", "E4", "E5"]);
  assert.equal(hist().past[1].label, "toggle the rest as wireframe");
  undoViewerAction();
  assert.equal(iso().contextWireframe, true);
  assert.deepEqual(iso().rows, [elementSetRowId("Deck")]);
});

test("isolating by name, and hiding / showing a set's elements", () => {
  assert.equal(isolateFeaSetsMatching("deck"), 1);
  assert.deepEqual(hidden(), ["E3", "E4", "E5"]);
  clearFeaIsolation();
  hideFeaRows([elementSetRowId("Side")]);
  assert.deepEqual(hidden(), ["E2", "E3"]);
  assert.equal(hist().past[hist().past.length - 1].label, "hide 2 objects");
  showFeaRows([elementSetRowId("Side")]);
  assert.deepEqual(hidden(), []);
});

test("the rows, a pick's sets and values, and the commands' reasons", () => {
  const view = feaTreeView();
  assert.ok(view.loaded);
  assert.deepEqual(view.roots.map((r) => r.name), ["Sets", "Properties"]);
  assert.deepEqual(feaPickedInfo("E2")?.sets.map((s) => s.name), ["Deck", "Side"]);
  assert.deepEqual(feaPickedInfo("E3")?.properties.map((p) => p.valueLabel), ["Alloy"]);
  assert.equal(feaPickedInfo("not-an-element"), null);

  registerCoreCommands();
  const reason = (id: string) => getCommand(id)!.disabledReason?.() ?? null;
  assert.equal(reason(ID.feaIsolateSets), null);
  assert.equal(reason(ID.feaClearIsolation), "Nothing is isolated");
  assert.equal(reason(ID.feaToggleContextWireframe), null);
  assert.equal(getCommand(ID.feaToggleContextWireframe)!.checked?.(), true);
  assert.equal(reason(ID.feaIsolateSelectionSets), "Pick an element that belongs to a set");
  useObjectInfoStore.setState({ name: "E2" });
  try {
    assert.equal(reason(ID.feaIsolateSelectionSets), null);
  } finally {
    useObjectInfoStore.setState({ name: null });
  }
  isolateFeaRows([elementSetRowId("Deck")]);
  assert.equal(reason(ID.feaClearIsolation), null);

  useFeaAnimationStore.setState({ manifest: null, sourceName: null });
  assert.equal(feaIsolationReason(), "No FEA result is loaded");
  assert.equal(feaContextReason(), "No FEA result is loaded");
  assert.equal(feaTreeView().loaded, false);
});
