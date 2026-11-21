// A scene state (`utils/scene/sceneRecord`): what the scene shows, put back through one path
// (`utils/scene/applySceneState`) in a fixed order, and kept per scope as saved states
// (`utils/scene/savedSceneStates`, `state/savedSceneStatesStore`).

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

const { applySceneState } = await import("@/utils/scene/applySceneState");
const { parseSceneRecord, serialiseSceneRecord, sceneRecordEmpty } = await import("@/utils/scene/sceneRecord");
const { applySceneStatesChange, EMPTY_SCENE_STATES_DOC, parseSceneStatesDoc, serialiseSceneStatesDoc, sceneStateSummary } =
  await import("@/utils/scene/savedSceneStates");
const { useSavedSceneStatesStore } = await import("@/state/savedSceneStatesStore");
type SceneRecord = import("@/utils/scene/sceneRecord").SceneRecord;
type SceneStateDeps = import("@/utils/scene/applySceneState").SceneStateDeps;
type SceneStatesDoc = import("@/utils/scene/savedSceneStates").SceneStatesDoc;

const FULL: SceneRecord = {
  v: 1,
  models: [
    { kind: "file", name: "models/frame.ifc" },
    { kind: "asset", provider: "prov", collection: "site", subject: "S1", revision: "r3" },
  ],
  camera: { v: 1, translation: null, position: [1, 2, 3], target: [0, 0, 0], up: [0, 0, 1] },
  view: { v: 1, sources: { "models/frame.ifc": { hidden: ["4"], selected: ["5"] } } },
  members: [{ target: { kind: "node", provider: "prov", collection: "site", subject: "S1", revision: "r3", node: null }, element: "beam-7", path: ["beam-7"] }],
  clash: { target: { kind: "file", sourceKey: "models/frame.ifc" }, resultKey: "_derived/clash/a/result.json" },
  detail: { glbKey: "_clash/details/d1/model.glb", statsKey: "_clash/details/d1/result.stats.json", spec: "builtin.box_joint" },
};

test("a state round-trips, and a v1 record without the new blocks still reads", () => {
  assert.deepEqual(parseSceneRecord(serialiseSceneRecord(FULL)), FULL);
  const old = parseSceneRecord(JSON.stringify({ v: 1, models: [{ kind: "file", name: "a.ifc" }], camera: null }));
  assert.deepEqual(old, { v: 1, models: [{ kind: "file", name: "a.ifc" }], camera: null });
});

test("malformed blocks are dropped, not the state", () => {
  const raw = JSON.stringify({
    ...FULL,
    members: [{ target: { kind: "node", collection: "site" }, element: null, path: [] }, FULL.members?.[0]],
    clash: { target: { kind: "file", sourceKey: "a.ifc" } },
    detail: { glbKey: "x.glb" },
  });
  const s = parseSceneRecord(raw);
  assert.deepEqual(s.members, FULL.members, "the member without a provider is dropped");
  assert.equal(s.clash, undefined, "a check with neither a result nor a run is dropped");
  assert.equal(s.detail, undefined);
  assert.equal(s.models.length, 2);
});

test("an empty state is told from one with something to restore", () => {
  assert.ok(sceneRecordEmpty({ v: 1, models: [], camera: { ...FULL.camera! } }));
  assert.ok(!sceneRecordEmpty({ v: 1, models: [], camera: null, detail: FULL.detail }));
});

function fakeDeps(over: Partial<SceneStateDeps> = {}) {
  const calls: string[] = [];
  const loaded = new Set<string>(["models/frame.ifc"]);
  const deps: SceneStateDeps = {
    loaded: () => loaded,
    loadModel: async (_s, m) => (calls.push(`model ${m.kind === "file" ? m.name : m.subject}`), true),
    loadMembers: async (_s, name, members) => (calls.push(`members ${name} ${members.length}`), []),
    applyView: async () => (calls.push("view"), null),
    restoreClash: async (_s, c, result) => void calls.push(`clash ${c.resultKey ?? c.runKey}${result ? " (given)" : ""}`),
    openDetail: async (_s, d) => void calls.push(`detail ${d.glbKey}`),
    applyCamera: () => void calls.push("camera"),
    ...over,
  };
  return { calls, deps };
}

test("a state is put back in order: models, members, view, clash check, detail model, camera", async () => {
  const { calls, deps } = fakeDeps();
  const out = await applySceneState(deps, "user:me", FULL, { membersName: "inputs" });
  assert.deepEqual(calls, [
    // The file is in the scene already: only the node is loaded.
    "model S1",
    "members inputs 1",
    "view",
    "clash _derived/clash/a/result.json",
    "detail _clash/details/d1/model.glb",
    "camera",
  ]);
  assert.deepEqual(out, { failed: [], notes: [], detailError: null });
});

test("a step that fails is said and the rest still runs; a detail model that fails is its own error", async () => {
  const { calls, deps } = fakeDeps({
    loadModel: async () => false,
    restoreClash: async () => {
      throw new Error("result gone");
    },
    openDetail: async () => {
      throw new Error("415");
    },
  });
  const out = await applySceneState(deps, "user:me", FULL);
  assert.equal(out.failed.length, 1, "the node is reported (the file is in the scene already)");
  assert.match(out.notes[0], /1 model could not be loaded/);
  assert.match(out.notes[1], /clash check could not be restored: result gone/);
  assert.equal(out.detailError, "415");
  assert.ok(calls.includes("camera"), "the camera still goes back");
});

test("a result the producer read already is handed on, not read again", async () => {
  const { calls, deps } = fakeDeps();
  await applySceneState(deps, "user:me", { v: 1, models: [], camera: null, clash: FULL.clash }, { clashResult: {} as never });
  assert.deepEqual(calls, ["clash _derived/clash/a/result.json (given)"]);
});

const saved = (id: string, name = id) => ({ id, name, created_at: "2026-11-20T10:00:00.000Z", state: FULL });

test("the saved states document keeps others' states through a change, and refuses another schema", () => {
  let doc = applySceneStatesChange(EMPTY_SCENE_STATES_DOC, { kind: "put", state: saved("s1") });
  doc = applySceneStatesChange(doc, { kind: "put", state: saved("s2") });
  doc = applySceneStatesChange(doc, { kind: "delete", id: "s1" });
  assert.deepEqual(doc.states.map((s) => s.id), ["s2"]);
  assert.deepEqual(parseSceneStatesDoc(serialiseSceneStatesDoc(doc)), doc);
  assert.deepEqual(parseSceneStatesDoc(""), EMPTY_SCENE_STATES_DOC);
  assert.throws(() => parseSceneStatesDoc(JSON.stringify({ schema: "ada.viewer/scene-states@9", states: [] })), /reads ada.viewer\/scene-states@1/);
  // A state that is not the known shape is dropped, the rest kept.
  const mixed = parseSceneStatesDoc(JSON.stringify({ schema: "ada.viewer/scene-states@1", states: [{ id: "x" }, saved("s3")] }));
  assert.deepEqual(mixed.states.map((s) => s.id), ["s3"]);
  assert.equal(sceneStateSummary(FULL), "2 models · 1 element · clash check · detail model · camera");
});

function storeDeps(captured: SceneRecord = FULL, leftOut = 0) {
  let doc: SceneStatesDoc = EMPTY_SCENE_STATES_DOC;
  const calls: string[] = [];
  return {
    calls,
    deps: {
      read: async () => doc,
      change: async (_s: string, change: Parameters<typeof applySceneStatesChange>[1]) => {
        calls.push(`change ${change.kind}`);
        doc = applySceneStatesChange(doc, change);
        return doc;
      },
      capture: async () => ({ state: captured, leftOut }),
      apply: async (_s: string, state: SceneRecord) => {
        calls.push(`apply ${sceneStateSummary(state)}`);
        return { failed: [], notes: ["1 hidden object was not found"], detailError: null };
      },
      now: () => new Date("2026-11-20T10:00:00Z"),
    },
  };
}

test("save, restore and delete a state; what a save leaves out and a restore misses is said", async () => {
  const { calls, deps } = storeDeps(FULL, 1);
  const store = useSavedSceneStatesStore;
  store.setState({ deps, states: [], scope: null, busyId: null, error: null, notes: [] });
  await store.getState().load("user:me");
  const s = await store.getState().save("user:me", "  deck  ", "someone");
  assert.equal(s?.name, "deck");
  assert.equal(s?.created_by, "someone");
  assert.equal(s?.created_at, "2026-11-20T10:00:00.000Z");
  assert.deepEqual(store.getState().states.map((x) => x.name), ["deck"]);
  assert.match(store.getState().notes[0] ?? "", /1 model is not stored in this scope/);

  await store.getState().restore("user:me", s!.id);
  assert.deepEqual(store.getState().notes, ["1 hidden object was not found"]);

  await store.getState().remove("user:me", s!.id);
  assert.deepEqual(store.getState().states, []);
  assert.deepEqual(calls, ["change put", `apply ${sceneStateSummary(FULL)}`, "change delete"]);
});

test("a scene with nothing a state can load again is not saved", async () => {
  const { calls, deps } = storeDeps({ v: 1, models: [], camera: FULL.camera });
  useSavedSceneStatesStore.setState({ deps, states: [], scope: "user:me", busyId: null, error: null, notes: [] });
  assert.equal(await useSavedSceneStatesStore.getState().save("user:me", "empty"), null);
  assert.deepEqual(calls, []);
  assert.match(useSavedSceneStatesStore.getState().notes[0] ?? "", /nothing was saved/);
});
