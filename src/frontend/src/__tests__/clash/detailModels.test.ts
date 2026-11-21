// The scope's kept detail models (Connections > Review, "Detail models"): listed per scope, loaded
// back the way a fresh detail run is loaded (the GLB overlaid, its joints added to the produced
// ones), deleted, and described in one line.

import assert from "node:assert/strict";
import { test } from "node:test";

const memory = new Map<string, string>();
const storage = {
  getItem: (k: string): string | null => (memory.has(k) ? (memory.get(k) as string) : null),
  setItem: (k: string, v: string): void => void memory.set(k, String(v)),
  removeItem: (k: string): void => void memory.delete(k),
  clear: (): void => memory.clear(),
  key: (): string | null => null,
  get length(): number {
    return memory.size;
  },
};
const globals = globalThis as unknown as { sessionStorage: unknown; localStorage: unknown };
globals.sessionStorage = storage;
globals.localStorage = storage;

const { useDetailModelsStore, detailModelLine, isDetailModelKey } = await import("@/state/detailModelsStore");
const { useClashCheckStore, contactRunToResult } = await import("@/state/clashCheckStore");
type Model = Parameters<typeof detailModelLine>[0];
type DetailContext = import("@/state/clashCheck/detailContext").DetailContext;
type SceneRecord = import("@/utils/scene/sceneRecord").SceneRecord;
type ApplyOptions = import("@/utils/scene/applySceneState").ApplyOptions;
type ApplyOutcome = import("@/utils/scene/applySceneState").ApplyOutcome;

function model(id: string, over: Partial<Model> = {}): Model {
  return {
    detail_id: id,
    created_at: "2026-11-19T10:15:00Z",
    status: "done",
    spec: "builtin.girder_gusset",
    source: { kind: "result", result_key: "_derived/clash/a/b/result.json", target: { kind: "file" }, label: "frame.ifc" },
    label: "builtin.girder_gusset · frame.ifc",
    glb_key: `_clash/details/${id}/model.glb`,
    stats_key: `_clash/details/${id}/result.stats.json`,
    counts: { joints: 2, welds: 6, plates: 2, skipped: 0 },
    ...over,
  };
}

function takeoff(names: readonly string[]) {
  return {
    joints: {
      count: names.length,
      by_type: [{ slug: "gusset", name: "Gusset", count: names.length }],
      items: names.map((name) => ({ name, slug: "gusset", type: "Gusset", members: ["g1", "g2"], plates: 1, welds: 3, centre: [0, 0, 1] })),
    },
    skipped: ["j9: refused"],
  };
}

type Applied = { scope: string; state: SceneRecord; opts?: ApplyOptions };

function fakeDeps(models: Model[]) {
  const calls: string[] = [];
  const applied: Applied[] = [];
  return {
    calls,
    applied,
    deps: {
      list: async (scope: string) => {
        calls.push(`list ${scope}`);
        return models;
      },
      remove: async (scope: string, id: string) => void calls.push(`remove ${scope} ${id}`),
      stats: async (_scope: string, key: string) => {
        calls.push(`stats ${key}`);
        return takeoff(["a1", "a2"]);
      },
      context: async (_scope: string, m: Model): Promise<DetailContext> => {
        calls.push(`context ${m.detail_id}`);
        throw new Error("no check");
      },
      apply: async (scope: string, state: SceneRecord, opts?: ApplyOptions): Promise<ApplyOutcome> => {
        calls.push(`apply ${scope}`);
        applied.push({ scope, state, opts });
        return { failed: [], notes: [], detailError: null };
      },
    },
  };
}

/** A check of a file, as `detailContext` rebuilds one. */
function fileCheck(): DetailContext {
  return {
    target: { kind: "file", sourceKey: "models/frame.ifc" },
    label: "models/frame.ifc",
    result: contactRunToResult(
      {
        schema: "ada.clash/contacts@1",
        run_id: "r1",
        created_at: "2026-11-19T12:00:00Z",
        engine: "wasm",
        checker: "convex-contact",
        target: { kind: "file", source_key: "models/frame.ifc" },
        geometry: { key: "_derived/models/frame.ifc.glb", sha256: "00", up_axis: "z" },
        options: { security_margin: 0.01, min_contact_area: 1e-6, line_contact_thickness: 1e-3 },
        members: [],
        contacts: [],
        provenance: {},
      } as never,
      "_derived/clash/a/b/result.json",
    ),
    derivedKey: "_derived/clash/a/b/result.json",
    runKey: null,
    members: [{ target: { kind: "file", source_key: "models/frame.ifc" }, element: null, path: [] }],
    notes: [],
  };
}

test("the list is the scope's, and a list that cannot be read is an empty one", async () => {
  const { deps } = fakeDeps([model("b"), model("a")]);
  useDetailModelsStore.setState({ deps, models: [], scope: null });
  await useDetailModelsStore.getState().load("user:me");
  assert.equal(useDetailModelsStore.getState().scope, "user:me");
  assert.deepEqual(useDetailModelsStore.getState().models.map((m) => m.detail_id), ["b", "a"]);

  useDetailModelsStore.setState({ deps: { ...deps, list: async () => Promise.reject(new Error("down")) } });
  await useDetailModelsStore.getState().load("project:other");
  assert.equal(useDetailModelsStore.getState().scope, "project:other");
  assert.deepEqual(useDetailModelsStore.getState().models, []);
});

test("loading one applies a scene state: the check's elements and result, then the model as the active detail", async () => {
  const { deps, calls, applied } = fakeDeps([]);
  const check = fileCheck();
  useDetailModelsStore.setState({ deps: { ...deps, context: async (_s, m) => (calls.push(`context ${m.detail_id}`), check) }, busyId: null, error: null });
  const m = model("x");
  await useDetailModelsStore.getState().open("user:me", m);
  assert.deepEqual(calls, ["context x", "apply user:me"]);
  const { state, opts } = applied[0];
  assert.deepEqual(state, {
    v: 1,
    models: [],
    camera: null,
    members: check.members,
    clash: { target: check.target, resultKey: check.derivedKey },
    detail: { glbKey: m.glb_key, statsKey: m.stats_key, spec: m.spec },
  });
  // The result read for the check is handed on, not read again; the members are named after it.
  assert.equal(opts?.clashResult, check.result);
  assert.equal(opts?.membersName, "models/frame.ifc");
  assert.deepEqual(useDetailModelsStore.getState().notes, []);
  assert.equal(useDetailModelsStore.getState().busyId, null);
});

test("a run's check is restored by its run key", async () => {
  const { deps, applied } = fakeDeps([]);
  const check = { ...fileCheck(), derivedKey: null, runKey: "_clash/runs/r1.json" };
  useDetailModelsStore.setState({ deps: { ...deps, context: async () => check }, busyId: null, error: null });
  await useDetailModelsStore.getState().open("user:me", model("x"));
  assert.deepEqual(applied[0].state.clash, { target: check.target, runKey: "_clash/runs/r1.json" });
});

test("a check that cannot be read is said, and the model loads on its own as before", async () => {
  const { deps, calls, applied } = fakeDeps([]);
  useDetailModelsStore.setState({ deps, busyId: null, error: null });
  const m = model("x");
  await useDetailModelsStore.getState().open("user:me", m);
  assert.deepEqual(calls, ["context x", "apply user:me"]);
  assert.deepEqual(applied[0].state, { v: 1, models: [], camera: null, detail: { glbKey: m.glb_key, statsKey: m.stats_key, spec: m.spec } });
  assert.match(useDetailModelsStore.getState().notes[0] ?? "", /could not be read: no check/);
  assert.equal(useDetailModelsStore.getState().error, null);
});

test("what the check and the restore could not bring back is said, in order", async () => {
  const { deps } = fakeDeps([]);
  const check = { ...fileCheck(), notes: ['The group "gone" is no longer saved in this scope, so its members were not loaded.'] };
  useDetailModelsStore.setState({
    deps: {
      ...deps,
      context: async () => check,
      apply: async () => ({ failed: [], notes: ["The elements could not be loaded: offline"], detailError: null }),
    },
    busyId: null,
    error: null,
  });
  await useDetailModelsStore.getState().open("user:me", model("x"));
  assert.deepEqual(useDetailModelsStore.getState().notes, [check.notes[0], "The elements could not be loaded: offline"]);
  assert.equal(useDetailModelsStore.getState().error, null);
});

test("a model whose GLB cannot be loaded says so as the load's error", async () => {
  const { deps } = fakeDeps([]);
  useDetailModelsStore.setState({
    deps: { ...deps, apply: async () => ({ failed: [], notes: [], detailError: "415" }) },
    busyId: null,
    error: null,
  });
  await useDetailModelsStore.getState().open("user:me", model("x"));
  assert.match(useDetailModelsStore.getState().error ?? "", /Could not load .*415/);
});

test("deleting one takes it off the list", async () => {
  const { deps, calls } = fakeDeps([]);
  useDetailModelsStore.setState({ deps, models: [model("b"), model("a")], scope: "user:me", busyId: null });
  await useDetailModelsStore.getState().remove("user:me", "b");
  assert.deepEqual(calls, ["remove user:me b"]);
  assert.deepEqual(useDetailModelsStore.getState().models.map((m) => m.detail_id), ["a"]);
});

test("a model's line says when, what from, and what it holds -- or how it ended", () => {
  assert.equal(detailModelLine(model("a")), "2026-11-19 10:15:00 · clash check of a file · 2 joints · 6 welds");
  assert.equal(
    detailModelLine(model("a", { source: { kind: "run", target: { kind: "group" }, label: "deck" }, counts: { joints: 1, welds: 1, skipped: 2 } })),
    "2026-11-19 10:15:00 · clash run on a group · 1 joint · 1 weld · 2 skipped",
  );
  assert.equal(detailModelLine(model("a", { status: "error", counts: null })), "2026-11-19 10:15:00 · clash check of a file · failed");
  assert.equal(detailModelLine(model("a", { status: "running", counts: null })), "2026-11-19 10:15:00 · clash check of a file · running");
});

test("a model's line says how many joints were not built and how many welds failed", () => {
  const counts = { joints: 3, welds: 9, skipped: 2, not_built: 2, weld_failures: 1 };
  assert.equal(
    detailModelLine(model("a", { counts })),
    "2026-11-19 10:15:00 · clash check of a file · 3 joints · 9 welds · 2 joints not built · 1 weld failed",
  );
  assert.equal(
    detailModelLine(model("a", { counts: { ...counts, not_built: 0, skipped: 0, weld_failures: 2 } })),
    "2026-11-19 10:15:00 · clash check of a file · 3 joints · 9 welds · 2 welds failed",
  );
});

test("a model's Not built report is read from its take-off once, and toggles", async () => {
  const { deps, calls } = fakeDeps([]);
  const doc = {
    ...takeoff(["a1"]),
    not_built: [{ joint_id: "j9", kind: "failed", spec: "provider.spec", reason: "ValueError: refused", declined: [] }],
    weld_failures: [{ joint_id: "a1", connection: "c", place: "web", weld_type: "fillet", reason: "no room" }],
  };
  useDetailModelsStore.setState({ deps: { ...deps, stats: async (_s: string, key: string) => (calls.push(`stats ${key}`), doc) }, reportId: null, reports: {} });
  const m = model("x");
  await useDetailModelsStore.getState().toggleReport("user:me", m);
  const s = useDetailModelsStore.getState();
  assert.equal(s.reportId, "x");
  const report = s.reports.x;
  assert.equal(report?.status, "ready");
  if (report?.status === "ready") {
    assert.deepEqual(report.stats.notBuilt.map((j) => [j.jointId, j.kind]), [["j9", "failed"]]);
    assert.deepEqual(report.stats.weldFailures.map((w) => [w.jointId, w.place, w.weldType]), [["a1", "web", "fillet"]]);
  }
  await useDetailModelsStore.getState().toggleReport("user:me", m);
  assert.equal(useDetailModelsStore.getState().reportId, null);
  await useDetailModelsStore.getState().toggleReport("user:me", m);
  assert.deepEqual(calls, [`stats ${m.stats_key}`], "read once");
});

test("a detail model is told from a model a clash check is of", () => {
  assert.ok(isDetailModelKey("_clash/details/20261119T101500000Z-0123abcd/model.glb"));
  assert.ok(isDetailModelKey("_derived/clash/detail/builtin.x/a/b/c/model.glb"));
  assert.ok(isDetailModelKey("_derived/clash/contacts-detail/builtin.x/a/b/c/model.glb"));
  assert.ok(!isDetailModelKey("models/frame.ifc"));
  assert.ok(!isDetailModelKey("_derived/models/frame.ifc.glb"));
  assert.ok(!isDetailModelKey(null));
});
