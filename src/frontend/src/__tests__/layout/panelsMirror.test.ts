// The Simulation panel, the result data table and the Scene panel in a pop-out window:
// the topics (`core:fea-controls`, `core:fea-table`, `core:scene-info`) round-trip through a
// fake bus, and the commands a pop-out sends run the main window's actions.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const { createWindowBus, memoryTransportHub, MAIN_WINDOW_ID } = await import("@/layout/windowBus");
const { followMirror, hostMirror, listMirrorTopics, resetMirror } = await import("@/layout/windowMirror");
const { useFeaAnimationStore } = await import("@/state/feaAnimationStore");
const { useColorStore } = await import("@/state/colorLegendStore");
const { useTableNavStore } = await import("@/state/tableNavStore");

function pair() {
  const hub = memoryTransportHub();
  const c = { t: 1000 };
  const main = createWindowBus({ id: MAIN_WINDOW_ID, label: "Main", transport: hub.connect(), now: () => c.t, timers: false });
  const w2 = createWindowBus({ id: "w2", label: "Window 2", transport: hub.connect(), now: () => c.t, timers: false });
  const queue: Array<() => void> = [];
  const schedule = (fn: () => void) => void queue.push(fn);
  const flush = () => {
    while (queue.length) queue.shift()!();
  };
  main.start();
  return { main, w2, schedule, flush };
}

const MANIFEST = {
  mesh: { n_points: 3 },
  fields: [{ name_canonical: "U", name_native: "U", kind: "vector3", components: ["x", "y", "z"], steps: [{ value: 1 }, { value: 2 }] }],
} as never;

function resetFea() {
  useFeaAnimationStore.getState().reset();
  useFeaAnimationStore.setState({ colormap: "abaqus", scaleFactor: 1, period: 2, showUndeformed: false, elementEdgesVisible: true, resultColorsVisible: true });
}

test("fea-controls: a snapshot carries the session without the scene's objects, and applies into the stores", async () => {
  const { feaControlsSnapshot, applyFeaControls, resetFeaControlsForTests } = await import("@/utils/scene/fea/feaControls");
  resetFeaControlsForTests();
  resetFea();
  useFeaAnimationStore.setState({
    sessionActive: true, sourceName: "r.bake", manifest: MANIFEST, fieldName: "U", reduction: "x", stepIndex: 1, nSteps: 2,
    factor: 0.5, scaleFactor: 4, isPlaying: true, colormap: "viridis", showUndeformed: true, mesh: { notCloneable: () => 1 } as never,
    applyStep: (async () => {}) as never,
  });
  useColorStore.setState({ min: -2, max: 9 });
  const snap = feaControlsSnapshot();
  // Plain data only: structured-clone safe, no mesh and no callbacks.
  assert.doesNotThrow(() => structuredClone(snap));
  assert.ok(!("mesh" in snap) && !("applyStep" in snap));
  assert.deepEqual(snap.legend, { min: -2, max: 9 });
  const wire = structuredClone(snap);
  resetFea();
  useFeaAnimationStore.setState({ applyStep: null });
  assert.equal(applyFeaControls(wire), true);
  const s = useFeaAnimationStore.getState();
  assert.equal(s.sessionActive, true);
  assert.equal(s.fieldName, "U");
  assert.equal(s.stepIndex, 1);
  assert.equal(s.scaleFactor, 4);
  assert.equal(s.colormap, "viridis");
  assert.equal(s.showUndeformed, true);
  assert.equal(s.isPlaying, true);
  assert.equal(s.manifest?.fields[0].name_canonical, "U");
  assert.equal(useColorStore.getState().max, 9);
  resetFea();
});

test("fea-controls: a change without news of the manifest leaves it out; a window that has none asks again", async () => {
  const { feaControlsTopic, resetFeaControlsForTests } = await import("@/utils/scene/fea/feaControls");
  resetFeaControlsForTests();
  resetFea();
  useFeaAnimationStore.setState({ sessionActive: true, sourceName: "r.bake", manifest: MANIFEST, fieldName: "U", nSteps: 2 });
  const topic = feaControlsTopic();
  const full = topic.snapshot();
  assert.ok("manifest" in full);
  useFeaAnimationStore.setState({ stepIndex: 1 });
  const delta = topic.delta!()!;
  assert.ok(!("manifest" in delta), "an unchanged manifest is not sent again");
  assert.equal(delta.stepIndex, 1);
  useFeaAnimationStore.setState({ manifest: { ...(MANIFEST as object) } as never });
  assert.ok("manifest" in topic.delta!()!);
  // A pop-out that missed the full snapshot refuses the delta (and so asks for the snapshot).
  resetFeaControlsForTests();
  assert.equal(topic.apply(delta), false);
  assert.equal(topic.apply(full), true);
  assert.equal(topic.apply(delta), true);
  resetFea();
});

test("fea-controls: the playing fields are throttled, the rest go at once", async () => {
  const { feaControlsTopic, resetFeaControlsForTests, PLAYING_PUBLISH_MS } = await import("@/utils/scene/fea/feaControls");
  resetFeaControlsForTests();
  resetFea();
  const timers: Array<[() => void, number]> = [];
  const topic = feaControlsTopic((fn, ms) => void timers.push([fn, ms]));
  let calls = 0;
  const off = topic.subscribe(() => void calls++);
  useFeaAnimationStore.setState({ isPlaying: true });
  assert.equal(calls, 1, "starting to play is a change");
  for (let i = 0; i < 10; i++) useFeaAnimationStore.setState({ factor: i / 10 });
  assert.equal(calls, 1, "frames wait");
  assert.equal(timers.length, 1);
  assert.equal(timers[0][1], PLAYING_PUBLISH_MS);
  timers[0][0]();
  assert.equal(calls, 2);
  useFeaAnimationStore.setState({ colormap: "gray" });
  assert.equal(calls, 3, "a toggle or a picker is not throttled");
  off();
  resetFea();
});

test("fea-controls: a pop-out's actions arrive as commands and run the main window's actions", async () => {
  const { remoteFeaControlActions, installFeaControls, localFeaControlActions, resetFeaControlsForTests, FEA_CONTROL_COMMANDS } = await import("@/utils/scene/fea/feaControls");
  resetMirror();
  resetFeaControlsForTests();
  resetFea();
  installFeaControls();
  const { main, w2, schedule, flush } = pair();
  hostMirror(main, schedule);
  w2.start();
  const calls: Array<[string, unknown]> = [];
  const real = { ...localFeaControlActions };
  const spy = localFeaControlActions as unknown as Record<string, unknown>;
  for (const name of Object.keys(FEA_CONTROL_COMMANDS)) spy[name] = (v?: unknown) => void calls.push([name, v]);
  try {
    const remote = remoteFeaControlActions(w2);
    remote.play();
    remote.pause();
    remote.stop();
    remote.step(-1);
    remote.setStep(3);
    remote.setScale(2.5);
    remote.selectField("S");
    remote.setColormap("viridis");
    remote.setWarp(false);
    remote.setResultColors(false);
    remote.setUndeformed(true);
    remote.setEdgeFade({ on: true, fullPx: 12 });
    remote.setContour({ min: 1, max: null, levels: 9 });
    remote.resetContourRange();
    flush();
    assert.deepEqual(calls, [
      ["play", undefined], ["pause", undefined], ["stop", undefined], ["step", -1], ["setStep", 3], ["setScale", 2.5], ["selectField", "S"],
      ["setColormap", "viridis"], ["setWarp", false], ["setResultColors", false], ["setUndeformed", true],
      ["setEdgeFade", { on: true, fullPx: 12 }], ["setContour", { min: 1, max: null, levels: 9 }], ["resetContourRange", undefined],
    ]);
    // A value of the wrong kind is dropped.
    calls.length = 0;
    const { sendMirrorCommand } = await import("@/layout/windowMirror");
    sendMirrorCommand(w2, "core:fea-set-step", { value: "3" });
    sendMirrorCommand(w2, "core:fea-set-scale", { value: Infinity });
    sendMirrorCommand(w2, "core:fea-set-warp", { value: "yes" });
    sendMirrorCommand(w2, "core:fea-set-contour", { value: { min: "a" } });
    sendMirrorCommand(w2, "core:fea-select-field", { value: "" });
    assert.deepEqual(calls, []);
  } finally {
    Object.assign(spy, real);
  }
  resetFea();
});

test("fea-controls: the same code runs in the main window (a local period change reaches the store)", async () => {
  const { localFeaControlActions } = await import("@/utils/scene/fea/feaControls");
  resetFea();
  localFeaControlActions.setPeriod(3);
  assert.equal(useFeaAnimationStore.getState().period, 3);
  localFeaControlActions.setPeriod(NaN);
  assert.equal(useFeaAnimationStore.getState().period, 3);
  localFeaControlActions.setUndeformed(true);
  localFeaControlActions.setElementEdges(false);
  assert.equal(useFeaAnimationStore.getState().showUndeformed, true);
  assert.equal(useFeaAnimationStore.getState().elementEdgesVisible, false);
  resetFea();
});

test("fea-table: the filter and the sort pick the rows, as the table itself picks them", async () => {
  const { tableRowIndices } = await import("@/utils/scene/fea/feaTableRows");
  const values = new Float32Array([5, 0, 3, 0, 1, 4]); // 3 nodes x 2 components
  assert.deepEqual([...tableRowIndices(3, "", null, values, 2, false)], [0, 1, 2]);
  assert.deepEqual([...tableRowIndices(3, "2", null, values, 2, false)], [1]);
  assert.deepEqual([...tableRowIndices(3, "", { col: 1, dir: "desc" }, values, 2, false)], [0, 1, 2]);
  assert.deepEqual([...tableRowIndices(3, "", { col: 1, dir: "asc" }, values, 2, false)], [2, 1, 0]);
  assert.deepEqual([...tableRowIndices(3, "", { col: 2, dir: "desc" }, values, 2, true)], [2, 0, 1]);
  assert.deepEqual([...tableRowIndices(3, "", { col: 0, dir: "desc" }, values, 2, false)], [2, 1, 0]);
});

test("fea-table: a watching window gets the first rows of the main window's query, with how many matched", async () => {
  const mirror = await import("@/utils/scene/fea/feaTableMirror");
  const { FEA_TABLE_TOPIC, FEA_TABLE_ROW_CAP, FEA_TABLE_WATCH_COMMAND, FEA_TABLE_QUERY_COMMAND, installFeaTable, resetFeaTableForTests, setFeaTableStepLoaderForTests, feaTableSnapshot, applyFeaTable, receivedFeaTable } = mirror;
  resetMirror();
  resetFeaTableForTests();
  resetFea();
  const nodes = FEA_TABLE_ROW_CAP + 500;
  const field = { name_canonical: "U", name_native: "U", kind: "vector3", components: ["x", "y", "z"], steps: [{ value: 1 }] };
  const values = new Float32Array(nodes * 3);
  for (let i = 0; i < nodes; i++) values.set([i, 2 * i, 3 * i], i * 3);
  let reads = 0;
  setFeaTableStepLoaderForTests(async () => (reads++, values));
  useFeaAnimationStore.setState({ sessionActive: true, sourceName: "r.bake", manifest: { mesh: { n_points: nodes }, fields: [field] } as never, fieldName: "U", stepIndex: 0, nSteps: 1 });
  const { main, w2, schedule, flush } = pair();
  installFeaTable(() => ["w2"]);
  hostMirror(main, schedule);
  w2.start();
  const { sendMirrorCommand } = await import("@/layout/windowMirror");
  // Nobody watches: the step is not read.
  assert.equal(feaTableSnapshot().state, "loading");
  assert.equal(reads, 0);
  sendMirrorCommand(w2, FEA_TABLE_WATCH_COMMAND, { on: true });
  flush();
  await new Promise((r) => setTimeout(r, 5));
  assert.equal(reads, 1);
  let snap = feaTableSnapshot();
  assert.equal(snap.state, "ready");
  assert.equal(snap.nPoints, nodes);
  assert.equal(snap.matched, nodes);
  assert.equal(snap.ids.length, FEA_TABLE_ROW_CAP, "rows are capped");
  assert.equal(snap.values.length, FEA_TABLE_ROW_CAP * 3);
  assert.equal(snap.ids[0], 1);
  // A filter narrows what matched; a sort reorders it; both are the main window's work.
  sendMirrorCommand(w2, FEA_TABLE_QUERY_COMMAND, { filter: "499", sort: { col: 0, dir: "desc" } });
  flush();
  snap = feaTableSnapshot();
  assert.ok(snap.matched < nodes && snap.matched === snap.ids.length);
  assert.ok(snap.ids.every((id) => String(id).includes("499")));
  assert.ok(snap.ids[0] > snap.ids[snap.ids.length - 1]);
  assert.equal(snap.values[0], (snap.ids[0] - 1));
  // A bad query is refused.
  sendMirrorCommand(w2, FEA_TABLE_QUERY_COMMAND, { filter: 5, sort: null });
  sendMirrorCommand(w2, FEA_TABLE_QUERY_COMMAND, { filter: "", sort: { col: -1, dir: "asc" } });
  assert.equal(feaTableSnapshot().query.filter, "499");
  // The topic is registered, and applies into the pop-out's table state and marked node.
  assert.ok(listMirrorTopics().some((t) => t.id === FEA_TABLE_TOPIC));
  assert.equal(applyFeaTable({ ...structuredClone(snap), activeNodeId: 7 }), true);
  assert.equal(receivedFeaTable()?.matched, snap.matched);
  assert.equal(useTableNavStore.getState().activeNodeId, 7);
  useTableNavStore.setState({ activeNodeId: null });
  // A window that closed without saying so stops being served.
  resetFeaTableForTests();
  resetFea();
});

test("fea-table: an element field lists nothing", async () => {
  const { feaTableSnapshot, resetFeaTableForTests } = await import("@/utils/scene/fea/feaTableMirror");
  resetFeaTableForTests();
  resetFea();
  const field = { name_canonical: "S", name_native: "S", kind: "scalar", components: ["s"], steps: [{ value: 1 }], per_type: [{ elem_type: "quad", n_elements: 4 }] };
  useFeaAnimationStore.setState({ sessionActive: true, sourceName: "r.bake", manifest: { mesh: { n_points: 4 }, fields: [field] } as never, fieldName: "S" });
  assert.equal(feaTableSnapshot().state, "idle");
  resetFea();
});

test("scene-info: models, groups (without members), take-off and boxes cross as data and apply into the stores", async () => {
  const { sceneInfoSnapshot, applySceneInfo, resetSceneInfoTopic, sceneInfoTopic, SCENE_INFO_GROUP_CAP } = await import("@/layout/sceneInfoMirror");
  const { useModelState } = await import("@/state/modelState");
  const { useSceneInfoStore } = await import("@/state/sceneInfoStore");
  const { useStatsStore } = await import("@/state/statsStore");
  const { useBBoxSelectionStore } = await import("@/state/bboxSelectionStore");
  const { useSceneMirrorStore } = await import("@/state/sceneMirrorStore");
  resetSceneInfoTopic();
  const group = (i: number) => ({ name: `g${i}`, type: "design" as const, parent_name: "P", members: ["a", "b", "c"], source: "m.glb" });
  useModelState.setState({ loadedSourceNames: new Set(["m.glb", "n.glb"]) });
  useSceneInfoStore.setState({ availableGroups: Array.from({ length: SCENE_INFO_GROUP_CAP + 10 }, (_, i) => group(i)), selectedGroup: group(1) });
  useBBoxSelectionStore.setState({ boxes: [{ id: "b1", label: "Box 1", min: [0, 0, 0], max: [1, 1, 1], mode: "partial", color: "#33aaff", opacity: 0.1, visible: true }], activeId: "b1" });
  useStatsStore.setState({ loading: false, available: true, stats: { objects: 3 } as never });
  const snap = sceneInfoSnapshot();
  assert.doesNotThrow(() => structuredClone(snap));
  assert.equal(snap.groups!.length, SCENE_INFO_GROUP_CAP, "groups are capped");
  assert.equal(snap.groupsTotal, SCENE_INFO_GROUP_CAP + 10);
  assert.ok(snap.groups!.every((g) => !("members" in g) && g.member_count === 3));
  assert.deepEqual(snap.models.map((m) => m.name), ["m.glb", "n.glb"]);
  const wire = structuredClone(snap);
  useModelState.setState({ loadedSourceNames: new Set() });
  useSceneInfoStore.setState({ availableGroups: [], selectedGroup: null });
  useBBoxSelectionStore.setState({ boxes: [], activeId: null });
  useStatsStore.setState({ stats: null, available: false });
  assert.equal(applySceneInfo(wire), true);
  assert.deepEqual([...useModelState.getState().loadedSourceNames], ["m.glb", "n.glb"]);
  assert.equal(useSceneInfoStore.getState().availableGroups.length, SCENE_INFO_GROUP_CAP);
  assert.equal(useSceneInfoStore.getState().selectedGroup?.member_count, 3);
  assert.equal(useSceneMirrorStore.getState().groupsTotal, SCENE_INFO_GROUP_CAP + 10);
  assert.equal(useBBoxSelectionStore.getState().activeId, "b1");
  assert.equal(useStatsStore.getState().available, true);
  // Groups go only when they change.
  const topic = sceneInfoTopic();
  topic.snapshot();
  useSceneInfoStore.setState({ selectedGroup: null });
  assert.ok(!("groups" in topic.delta!()!));
  resetSceneInfoTopic();
  assert.equal(applySceneInfo({ ...structuredClone(snap), groups: undefined }), false, "a message without groups, before the first with them, is refused");
  useModelState.setState({ loadedSourceNames: new Set() });
  useSceneInfoStore.setState({ availableGroups: [], selectedGroup: null });
  useBBoxSelectionStore.setState({ boxes: [], activeId: null });
  useStatsStore.setState({ stats: null, available: false });
});

test("scene actions for the Scene panel: a pop-out's request runs the main window's action, with checked arguments", async () => {
  const { registerSceneActionCommands, resetSceneActionCommands, localSceneActions, remoteSceneActions } = await import("@/layout/sceneActions");
  resetSceneActionCommands();
  resetMirror();
  registerSceneActionCommands(() => []);
  const { main, w2, schedule, flush } = pair();
  hostMirror(main, schedule);
  w2.start();
  const seen: Array<[string, ...unknown[]]> = [];
  const real = { ...localSceneActions };
  const a = localSceneActions as unknown as Record<string, unknown>;
  a.setModelVisible = (s: string, v: boolean) => void seen.push(["visible", s, v]);
  a.unloadModels = (s: string[]) => void seen.push(["unload", s]);
  a.selectGroup = (g: unknown) => void seen.push(["group", g]);
  a.updateBox = (id: string, p: unknown) => void seen.push(["update", id, p]);
  a.removeBox = (id: string) => void seen.push(["remove", id]);
  a.activateBox = (id: string) => void seen.push(["activate", id]);
  a.clearBoxes = () => void seen.push(["clear"]);
  a.boxAroundSelection = () => void seen.push(["around"]);
  try {
    const r = remoteSceneActions(w2);
    r.setModelVisible("m.glb", false);
    r.unloadModels(["m.glb", "n.glb"]);
    r.selectGroup({ source: "m.glb", name: "g", parent_name: "P" });
    r.selectGroup(null);
    r.updateBox("b1", { visible: false, mode: "full" });
    r.removeBox("b1");
    r.activateBox("b2");
    r.clearBoxes();
    r.boxAroundSelection();
    flush();
    assert.deepEqual(seen, [
      ["visible", "m.glb", false],
      ["unload", ["m.glb", "n.glb"]],
      ["group", { source: "m.glb", name: "g", parent_name: "P" }],
      ["group", null],
      ["update", "b1", { visible: false, mode: "full" }],
      ["remove", "b1"],
      ["activate", "b2"],
      ["clear"],
      ["around"],
    ]);
    // Wrong kinds are dropped; a patch keeps only what a pop-out may change.
    seen.length = 0;
    const { sendMirrorCommand } = await import("@/layout/windowMirror");
    sendMirrorCommand(w2, "core:model-visible", { source: 3, visible: true });
    sendMirrorCommand(w2, "core:unload-models", { sources: [1, 2] });
    sendMirrorCommand(w2, "core:select-group", { group: { name: 1 } });
    sendMirrorCommand(w2, "core:box-update", { id: "b1", patch: { min: [9, 9, 9], visible: true } });
    assert.deepEqual(seen, [["update", "b1", { visible: true }]]);
  } finally {
    Object.assign(a, real);
  }
});

test("group selection names a group by model, name and owner", async () => {
  const { sameGroup } = await import("@/utils/groups/applyGroupSelection");
  assert.equal(sameGroup({ source: "a", name: "g", parent_name: "p" }, { source: "a", name: "g", parent_name: "p" }), true);
  assert.equal(sameGroup({ name: "g", parent_name: "p" }, { source: null, name: "g", parent_name: "p" }), true);
  assert.equal(sameGroup({ source: "a", name: "g", parent_name: "p" }, { source: "b", name: "g", parent_name: "p" }), false);
});

test("core registers the three topics", async () => {
  resetMirror();
  const { registerCoreMirror, resetCoreMirror } = await import("@/layout/windowMirrorCore");
  const { resetSceneActionCommands } = await import("@/layout/sceneActions");
  resetCoreMirror();
  resetSceneActionCommands();
  registerCoreMirror();
  const ids = listMirrorTopics().map((t) => t.id);
  for (const id of ["core:fea-controls", "core:fea-table", "core:scene-info"]) assert.ok(ids.includes(id), id);
  resetCoreMirror();
});
