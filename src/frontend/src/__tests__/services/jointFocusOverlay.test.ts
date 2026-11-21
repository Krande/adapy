// A joint shown in Connections > Review is drawn with overlays of ITS OWN, through the viewport on
// screen: its members' axes and labels and its welds' labels where its switches say, its welds in its
// weld colour or hidden -- and nothing of a joint not shown. Positions come from the model's own
// object metadata (a beam's `axis`, a weld's `at`), placed at the source's origin. A viewport that
// records what it is asked to draw stands in for either one.

import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

import type { Billboard } from "@/viewport/api/billboards";
import type { OverlayLines } from "@/viewport/api/overlayLayer";
import type { ObjectRef, ViewportApi } from "@/viewport/api/viewportApi";

await import("../fixtures/panelRealm");
const { clearJointFocusOverlay, drawFocusedJoints } = await import("@/utils/scene/jointFocusOverlay");
const { useConnectionGraphStore } = await import("@/state/connectionGraphStore");
const { useJointFocusStore } = await import("@/state/jointFocusStore");
const { useLineageStore } = await import("@/state/lineageStore");
const { useModelState } = await import("@/state/modelState");
const { useTreeViewStore } = await import("@/state/treeViewStore");
const { setActiveViewport } = await import("@/viewport/api/viewportApi");

const FILE = "detail.glb";
const ORIGIN: [number, number, number] = [10, 20, 30];

function recordingViewport() {
  const lines: OverlayLines[] = [];
  const labels: Billboard[] = [];
  const hidden: ObjectRef[] = [];
  const marks: Map<string, Map<string, string>>[] = [];
  const viewport = {
    kind: "native",
    sourceOrigin: (name: string) => (name === FILE ? ORIGIN : null),
    objectsCentre: () => null,
    selection: () => [],
    onSelectionChange: () => () => {},
    hide: (refs: Iterable<ObjectRef>) => hidden.push(...refs),
    show: () => {},
    setObjectMarks: (m: Map<string, Map<string, string>>) => marks.push(m),
    clearModels: () => {},
    overlays: () => ({ addLines: (l: OverlayLines) => lines.push(l), dispose: () => void (lines.length = 0) }),
    billboards: () => ({ add: (b: Billboard) => labels.push(b), update: () => {}, dispose: () => void (labels.length = 0) }),
  } as unknown as ViewportApi;
  return { viewport, lines, labels, hidden, marks };
}

const EXT = {
  design_objects: [
    {
      connections: [
        { name: "j1", spec_name: "s", member_roles: { incoming: ["beam"] }, weld_names: ["w1"] },
        { name: "j2", spec_name: "s", member_roles: { incoming: ["beam2"] }, weld_names: ["w2"] },
      ],
    },
  ],
};

let withdraw: () => void = () => {};
let rec: ReturnType<typeof recordingViewport>;

beforeEach(() => {
  rec = recordingViewport();
  withdraw = setActiveViewport(rec.viewport);
  useModelState.setState({ loadedSourceNames: new Set([FILE]) });
  useConnectionGraphStore.getState().clearIndex();
  useConnectionGraphStore.getState().register(FILE, EXT);
  useLineageStore.getState().clear();
  useLineageStore.getState().register({
    kind: "cad",
    fileName: FILE,
    assemblyGuid: "g1",
    root: null,
    objectGuids: { beam: "b" },
    objectMetadata: {
      beam: { type: "Beam", section: { name: "IPE300" }, axis: { n1: [0, 0, 0], n2: [1, 0, 0], up: [0, 0, 1] } },
      w1: { type: "weld", weld_type: "FILLET", throat: 0.005, members: ["beam"], at: [0, 0, 1] },
      beam2: { type: "Beam", section: { name: "HEB200" }, axis: { n1: [5, 0, 0], n2: [6, 0, 0], up: [0, 0, 1] } },
      w2: { type: "weld", weld_type: "FILLET", throat: 0.005, members: ["beam2"], at: [5, 0, 1] },
    },
  } as never);
  useTreeViewStore.setState({
    treeData: {
      id: "root",
      name: FILE,
      source_name: FILE,
      model_key: "m1",
      children: [
        { id: "n1", name: "w1", rangeId: 7, children: [] },
        { id: "n2", name: "w2", rangeId: 8, children: [] },
      ],
    } as never,
  });
  useJointFocusStore.getState().clear();
});

afterEach(() => {
  clearJointFocusOverlay();
  withdraw();
});

test("only the joint shown is drawn, with its axes, labels and weld colour", () => {
  assert.equal(drawFocusedJoints(), 0, "no joint shown: nothing drawn");
  useJointFocusStore.getState().focus(FILE, "j1");
  assert.equal(drawFocusedJoints(), 2, "one member label and one weld label");
  assert.equal(rec.lines.length, 1);
  assert.deepEqual([...rec.lines[0].segments], [10, 20, 30, 11, 20, 30], "the beam's axis, at the source's origin");
  assert.deepEqual(rec.labels[1].position, [10, 20, 31], "the weld's label at its `at`");
  const lastMarks = rec.marks.at(-1)!;
  assert.deepEqual([...(lastMarks.get("m1")?.keys() ?? [])], ["7"], "only j1's weld is coloured");
});

test("each joint keeps its own switches", () => {
  const focus = useJointFocusStore.getState();
  focus.focus(FILE, "j1");
  focus.focus(FILE, "j2");
  focus.setOverlay(FILE, "j2", "axes", false);
  focus.setOverlay(FILE, "j2", "welds", false);
  focus.setOverlay(FILE, "j2", "showWelds", false);
  assert.equal(drawFocusedJoints(), 2, "j1's marks only");
  assert.deepEqual(rec.hidden, [{ model: "m1", id: "8" }], "j2's weld is hidden, j1's is not");
  focus.remove(FILE, "j1");
  assert.equal(drawFocusedJoints(), 0);
});

test("a clear of the scene takes the shown joints at once; a replace that keeps the model keeps their weld colour", async () => {
  const { clearScene } = await import("@/utils/scene/handlers/clear_scene");
  useJointFocusStore.getState().focus(FILE, "j1");
  assert.equal(drawFocusedJoints(), 2);
  const weldColoured = () => [...(rec.marks.at(-1)?.get("m1")?.keys() ?? [])];
  assert.deepEqual(weldColoured(), ["7"]);
  // The three.js-free viewport's replace swaps the model in place: its joints stay, and so does the
  // colour of their welds (it went with the plugins' highlights, which a replace takes).
  await clearScene({ reason: "replace", replacing: true, keepQueuedLoads: true, keepProcedural: true, keepLoadedSources: true });
  assert.deepEqual(weldColoured(), ["7"], "the weld colour stays");
  assert.equal(rec.lines.length, 1);
  // A clear takes them in the same turn, not a turn later when the store would drop them.
  await clearScene({ reason: "test" });
  assert.equal(rec.lines.length + rec.labels.length, 0, "nothing of the joint is left over the cleared scene");
  assert.deepEqual(weldColoured(), []);
  assert.equal(useJointFocusStore.getState().joints.length, 0);
});

test("a joint whose model is not on screen draws nothing", () => {
  useJointFocusStore.getState().focus("elsewhere.glb", "j1");
  assert.equal(drawFocusedJoints(), 0);
  assert.equal(rec.lines.length + rec.labels.length, 0);
});
