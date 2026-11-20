// The viewer's side of `/api/viewer/...`: the commands an agent sends (services/viewerControl/
// commands.ts) run against the viewer's own functions -- `focus_joint` through the joints review's
// focus, `open_detail_model` through the Detail models list's Load -- and answer with what the
// viewer shows, one command or a batch; and a tab takes part only while the server and the tab both
// allow it. The transport is services/viewerControl/socket.ts (viewerControlSocket.test.ts).

import assert from "node:assert/strict";
import { test } from "node:test";

import type { WireDetailModel } from "@/services/api/clashCheck";
import {
  collectViewerState,
  runViewerCommand,
  type ViewerCommand,
  type ViewerControlDeps,
} from "@/services/viewerControl/commands";
import type { ObjectRef, ViewportApi } from "@/viewport/api/viewportApi";

const JOINT = "0123456789ab";
const DECLINED = "ba9876543210";
const RUN = "_clash/0000/run.json";
const SOURCE = "models/frame.glb";
const DETAIL_ID = "20260101T000000000Z-0123abcd";
const DETAIL_GLB = `_clash/details/${DETAIL_ID}/model.glb`;

function fakeViewport(log: string[]) {
  let selection: ObjectRef[] = [];
  const vp = {
    kind: "three",
    selectionCount: () => selection.length,
    selection: () => selection,
    setSelection: (refs: Iterable<ObjectRef>) => {
      selection = [...refs].map((r) => ({ ...r }));
      log.push(`setSelection:${selection.length}`);
      return selection.length;
    },
    clearSelection: () => {
      selection = [];
      log.push("clearSelection");
    },
    selectionBounds: () => (selection.length ? { min: [0, 0, 0], max: [1, 2, 3] } : null),
    captureState: () => ({ selected: selection.length, saved: [...selection] }),
    restoreState: (s: { saved: ObjectRef[] }) => {
      selection = s.saved;
      log.push("restoreState");
    },
    objectsCentre: () => [0.5, 1, 1.5],
    cameraPose: () => ({ eye: [10, 10, 10], target: [0, 0, 0], up: [0, 0, 1], fovDeg: 50 }),
    fitSelection: () => log.push("fitSelection"),
    fitAll: () => log.push("fitAll"),
    setView: (v: string) => log.push(`setView:${v}`),
    setCameraPose: (eye: number[], target: number[]) => log.push(`setCameraPose:${eye}->${target}`),
    fitBox: () => (log.push("fitBox"), true),
    select(refs: ObjectRef[]) {
      selection = refs;
    },
  };
  return vp as unknown as ViewportApi & { select(refs: ObjectRef[]): void };
}

function detailModel(over: Partial<WireDetailModel> = {}): WireDetailModel {
  return {
    detail_id: DETAIL_ID,
    created_at: "2026-01-01T00:00:00Z",
    status: "done",
    spec: "provider.high",
    source: { kind: "run", run_key: RUN },
    label: "provider.high · frame",
    glb_key: DETAIL_GLB,
    stats_key: `_clash/details/${DETAIL_ID}/result.stats.json`,
    ...over,
  } as WireDetailModel;
}

function fakeDeps(over: Partial<ViewerControlDeps> = {}) {
  const log: string[] = [];
  const vp = fakeViewport(log);
  const loaded = new Set([SOURCE]);
  const clash = { runKey: null as string | null, selected: null as string | null, joints: new Set<string>() };
  const deps: ViewerControlDeps = {
    viewport: () => vp,
    scope: () => "user:me",
    loadedSources: () => [...loaded],
    activeSource: () => SOURCE,
    clash: () => ({
      runKey: clash.runKey,
      joints: clash.runKey ? clash.joints.size : null,
      selectedJoint: clash.selected,
      hasJoint: (id) => clash.joints.has(id),
    }),
    openRun: async (scope, key) => {
      log.push(`openRun:${scope}:${key}`);
      clash.runKey = key;
      clash.joints = new Set([JOINT, DECLINED]);
    },
    focusJoint: async (id, reveal) => {
      log.push(`focusJoint:${id}:${reveal}`);
      clash.selected = id;
      vp.select([{ model: "m1", id: "1" }, { model: "m1", id: "2" }]);
    },
    selectNames: async (file, names) => {
      log.push(`selectNames:${file}:${names.join(",")}`);
      vp.select(names.includes("missing") ? [] : names.map((n, i) => ({ model: file, id: String(i), node: n })));
    },
    objectsNamed: async (_file, name) => (name === "missing" ? [] : [{ model: "m1", id: "7", node: name }]),
    listDetailModels: async () => [detailModel()],
    openDetailModel: async (_scope, model) => {
      log.push(`openDetailModel:${model.detail_id}`);
      loaded.add(model.glb_key);
    },
    detailConnections: async (_scope, _model, jointId) => (jointId === JOINT ? [`provider.high_${JOINT}`] : []),
    settle: async () => {},
    ...over,
  };
  return { deps, log, vp, clash, loaded };
}

const run = (deps: ViewerControlDeps, c: ViewerCommand) => runViewerCommand(deps, c);

test("focus_joint opens the run it names, focuses the joint as the review does, and frames it", async () => {
  const { deps, log } = fakeDeps();
  const out = await run(deps, { action: "focus_joint", joint_id: JOINT, run_key: RUN, frame: true, reveal_panel: true });
  assert.deepEqual(out, { joint_id: JOINT, selected: 2 });
  assert.deepEqual(log, [`openRun:user:me:${RUN}`, `focusJoint:${JOINT}:true`, "fitSelection"]);
  const state = collectViewerState(deps);
  assert.equal(state.clash.selected_joint, JOINT);
  assert.equal(state.clash.run_key, RUN);
  assert.equal(state.selection.count, 2);
});

test("focus_joint on a joint the open result does not have says so", async () => {
  const { deps } = fakeDeps();
  await assert.rejects(run(deps, { action: "focus_joint", joint_id: JOINT }), /not in the clash result open here/);
});

test("select picks names in the active model and frames them; nothing found is an error", async () => {
  const { deps, log } = fakeDeps();
  const out = (await run(deps, { action: "select", names: ["column_1", "brace_1"] })) as { selected: number; bounds: unknown };
  assert.equal(out.selected, 2);
  assert.deepEqual(out.bounds, { min: [0, 0, 0], max: [1, 2, 3] });
  assert.deepEqual(log, [`selectNames:${SOURCE}:column_1,brace_1`, "fitSelection"]);
  await assert.rejects(run(deps, { action: "select", names: ["missing"] }), /none of 1 name/);
  await assert.rejects(run(deps, { action: "select", names: ["column_1"], file: "models/other.glb" }), /not loaded/);
});

test("camera does one move", async () => {
  const { deps, log } = fakeDeps();
  await run(deps, { action: "camera", fit: "all" });
  await run(deps, { action: "camera", view: "top" });
  await run(deps, { action: "camera", eye: [5, 5, 5], target: [0, 0, 0] });
  await run(deps, { action: "camera", box: { min: [0, 0, 0], max: [1, 1, 1] } });
  assert.deepEqual(log, ["fitAll", "setView:top", "setCameraPose:5,5,5->0,0,0", "fitBox"]);
  await assert.rejects(run(deps, { action: "camera", fit: "selection" }), /nothing is selected/);
});

test("find_objects measures each name and puts the selection back", async () => {
  const { deps, log, vp } = fakeDeps();
  vp.select([{ model: "m1", id: "99" }]);
  const out = await run(deps, { action: "find_objects", names: ["column_1", "missing"] });
  assert.deepEqual(out, {
    found: [{ name: "column_1", file: SOURCE, objects: 1, centre: [0.5, 1, 1.5], bounds: { min: [0, 0, 0], max: [1, 2, 3] } }],
    missing: ["missing"],
  });
  assert.equal(log.at(-1), "restoreState");
  assert.deepEqual(vp.selection(), [{ model: "m1", id: "99" }]);
});

test("open_detail_model loads the model and selects the joint's connection in it", async () => {
  const { deps, log, loaded } = fakeDeps();
  const out = await run(deps, { action: "open_detail_model", detail_id: DETAIL_ID, joint_id: JOINT });
  assert.ok(loaded.has(DETAIL_GLB));
  assert.deepEqual(out, { detail_id: DETAIL_ID, joint_id: JOINT, shown_in: "detail_model", connections: [`provider.high_${JOINT}`] });
  assert.deepEqual(log, [`openDetailModel:${DETAIL_ID}`, `selectNames:${DETAIL_GLB}:provider.high_${JOINT}`, "fitSelection"]);
  assert.deepEqual(collectViewerState(deps).detail_models_loaded, [DETAIL_ID]);
});

test("open_detail_model shows a joint the model does not hold in the run it was detailed from", async () => {
  const { deps, log } = fakeDeps();
  const out = await run(deps, { action: "open_detail_model", detail_id: DETAIL_ID, joint_id: DECLINED });
  assert.deepEqual(out, { detail_id: DETAIL_ID, joint_id: DECLINED, shown_in: "source_model", run_key: RUN, selected: 2 });
  assert.deepEqual(log, [`openDetailModel:${DETAIL_ID}`, `openRun:user:me:${RUN}`, `focusJoint:${DECLINED}:true`, "fitSelection"]);
});

test("open_detail_model refuses a model not built yet, or unknown", async () => {
  const { deps } = fakeDeps({ listDetailModels: async () => [detailModel({ status: "running" })] });
  await assert.rejects(run(deps, { action: "open_detail_model", detail_id: DETAIL_ID }), /running, not built yet/);
  await assert.rejects(run(deps, { action: "open_detail_model", detail_id: "20990101T000000000Z-ffffffff" }), /no detail model/);
});

test("a command's answer carries its outcome and the state; a failure is answered as an error", async () => {
  const { answerPayload } = await import("@/services/viewerControl/answer");
  const { deps } = fakeDeps({ viewport: () => null });
  const ok = (await answerPayload(deps, { command: { action: "get_state" } })) as { ok: boolean; state: { loaded_models: string[] } };
  const err = (await answerPayload(deps, { command: { action: "clear_selection" } })) as {
    ok: boolean;
    error: string | null;
    state: { loaded_models: string[] };
  };
  assert.equal(ok.ok, true);
  assert.deepEqual(ok.state.loaded_models, [SOURCE]);
  assert.equal(err.ok, false);
  assert.match(err.error ?? "", /no 3D view/);
  assert.deepEqual(err.state.loaded_models, [SOURCE]);
});

test("a joint's connections in a take-off are the ones named after it", async () => {
  const { connectionsOfJoint } = await import("@/services/viewerControl/realDeps");
  const names = [`provider.high_${JOINT}`, `provider.high_${JOINT}_1`, `provider.high_${DECLINED}`, `x_${JOINT}9`];
  assert.deepEqual(connectionsOfJoint(names, JOINT), [`provider.high_${JOINT}`, `provider.high_${JOINT}_1`]);
});
test("a batch runs in order and stops at the first failure: the rest are skipped", async () => {
  const { answerPayload } = await import("@/services/viewerControl/answer");
  const { deps, log } = fakeDeps();
  const answer = await answerPayload(deps, {
    commands: [
      { action: "camera", fit: "all" },
      { action: "focus_joint", joint_id: JOINT },
      { action: "camera", view: "top" },
    ],
  });
  assert.equal(answer.ok, false);
  assert.match(answer.error ?? "", /command 2 of 3 \(focus_joint\) failed: joint 0123456789ab is not in the clash result/);
  assert.deepEqual(
    answer.steps?.map((s) => [s.action, s.status]),
    [
      ["camera", "done"],
      ["focus_joint", "error"],
      ["camera", "skipped"],
    ],
  );
  assert.deepEqual(log, ["fitAll"], "the third command never ran");

  const ok = await answerPayload(deps, {
    commands: [
      { action: "focus_joint", joint_id: JOINT, run_key: RUN },
      { action: "camera", view: "isometric" },
    ],
  });
  assert.equal(ok.ok, true);
  assert.equal(ok.error, null);
  assert.deepEqual(ok.steps?.map((s) => s.status), ["done", "done"]);
  assert.deepEqual(ok.steps?.[0].result, { joint_id: JOINT, selected: 2 });
});

test("off on the server: no socket, whatever the switch says", async () => {
  const { followExternalControls } = await import("@/services/viewerControl/gate");
  const { useExternalControlsStore } = await import("@/state/externalControlsStore");
  const events: string[] = [];
  useExternalControlsStore.setState({ chosen: true });
  const unfollow = followExternalControls(() => (events.push("start"), () => void events.push("stop")), () => "off");
  assert.deepEqual(events, []);
  unfollow();
  useExternalControlsStore.setState({ chosen: null });
});

test("opt-in: the switch starts off; the tab connects once turned on, and closes when turned off", async () => {
  const { followExternalControls } = await import("@/services/viewerControl/gate");
  const { useExternalControlsStore, externalControlsOn } = await import("@/state/externalControlsStore");
  const events: string[] = [];
  useExternalControlsStore.setState({ chosen: null });
  assert.equal(externalControlsOn(null, "opt-in"), false);
  const unfollow = followExternalControls(() => (events.push("start"), () => void events.push("stop")), () => "opt-in");
  assert.deepEqual(events, []);
  useExternalControlsStore.getState().setEnabled(true);
  assert.deepEqual(events, ["start"]);
  useExternalControlsStore.getState().setEnabled(false);
  assert.deepEqual(events, ["start", "stop"]);
  useExternalControlsStore.getState().setEnabled(true);
  unfollow();
  assert.deepEqual(events, ["start", "stop", "start", "stop"]);
  useExternalControlsStore.setState({ chosen: null });
});

test("auto: the tab connects untouched; switched off it stays off", async () => {
  const { followExternalControls } = await import("@/services/viewerControl/gate");
  const { useExternalControlsStore, externalControlsOn } = await import("@/state/externalControlsStore");
  const events: string[] = [];
  useExternalControlsStore.setState({ chosen: null });
  assert.equal(externalControlsOn(null, "auto"), true);
  const unfollow = followExternalControls(() => (events.push("start"), () => void events.push("stop")), () => "auto");
  assert.deepEqual(events, ["start"], "on without touching the switch");
  useExternalControlsStore.getState().setEnabled(false);
  assert.deepEqual(events, ["start", "stop"]);
  assert.equal(externalControlsOn(useExternalControlsStore.getState().chosen, "auto"), false, "a tab switched off stays off");
  unfollow();
  assert.deepEqual(events, ["start", "stop"]);
  useExternalControlsStore.setState({ chosen: null });
});

test("the page reads the server's mode from /config.js", async () => {
  const { viewerControlModeOf } = await import("@/runtime/config");
  assert.equal(viewerControlModeOf(undefined), "off");
  assert.equal(viewerControlModeOf("opt-in"), "opt-in");
  assert.equal(viewerControlModeOf(true), "opt-in");
  assert.equal(viewerControlModeOf("auto"), "auto");
  assert.equal(viewerControlModeOf("something else"), "off");
});

test("the switch is not chosen until the user touches it", async () => {
  const { useExternalControlsStore } = await import("@/state/externalControlsStore");
  assert.equal(useExternalControlsStore.getInitialState().chosen, null);
});
