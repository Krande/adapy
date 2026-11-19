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
const { useClashCheckStore } = await import("@/state/clashCheckStore");
type Model = Parameters<typeof detailModelLine>[0];

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

function fakeDeps(models: Model[]) {
  const calls: string[] = [];
  return {
    calls,
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
      overlay: async (scope: string, glb: string) => void calls.push(`overlay ${scope} ${glb}`),
    },
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

test("loading one overlays its GLB and adds its joints to the produced ones, as a fresh run does", async () => {
  const { deps, calls } = fakeDeps([]);
  useDetailModelsStore.setState({ deps, busyId: null, error: null });
  useClashCheckStore.setState({ producedJoints: null, producedSkipped: [] });
  const m = model("x");
  await useDetailModelsStore.getState().open("user:me", m);
  assert.deepEqual(calls, [`overlay user:me ${m.glb_key}`, `stats ${m.stats_key}`]);
  const produced = useClashCheckStore.getState().producedJoints;
  assert.equal(produced?.count, 2);
  assert.deepEqual(useClashCheckStore.getState().producedSkipped, ["j9: refused"]);
  // Loading it again lists its joints once.
  await useDetailModelsStore.getState().open("user:me", m);
  assert.equal(useClashCheckStore.getState().producedJoints?.count, 2);
  assert.deepEqual(useClashCheckStore.getState().producedSkipped, ["j9: refused"]);
  assert.equal(useDetailModelsStore.getState().busyId, null);
});

test("a model whose GLB cannot be loaded says so and adds no joints", async () => {
  const { deps } = fakeDeps([]);
  useDetailModelsStore.setState({ deps: { ...deps, overlay: async () => Promise.reject(new Error("415")) }, busyId: null, error: null });
  useClashCheckStore.setState({ producedJoints: null, producedSkipped: [] });
  await useDetailModelsStore.getState().open("user:me", model("x"));
  assert.match(useDetailModelsStore.getState().error ?? "", /Could not load .*415/);
  assert.equal(useClashCheckStore.getState().producedJoints, null);
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

test("a detail model is told from a model a clash check is of", () => {
  assert.ok(isDetailModelKey("_clash/details/20261119T101500000Z-0123abcd/model.glb"));
  assert.ok(isDetailModelKey("_derived/clash/detail/builtin.x/a/b/c/model.glb"));
  assert.ok(isDetailModelKey("_derived/clash/contacts-detail/builtin.x/a/b/c/model.glb"));
  assert.ok(!isDetailModelKey("models/frame.ifc"));
  assert.ok(!isDetailModelKey("_derived/models/frame.ifc.glb"));
  assert.ok(!isDetailModelKey(null));
});
