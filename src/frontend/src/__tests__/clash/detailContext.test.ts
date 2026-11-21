// What a kept detail model was made from (`state/clashCheck/detailContext.ts`): the check behind it
// -- its target and its result -- and the elements to load, rebuilt from a stored clash result's
// provenance or a stored convex-contact run.

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

const { detailContext } = await import("@/state/clashCheck/detailContext");
type Deps = Parameters<typeof detailContext>[0];
type Model = Parameters<typeof detailContext>[2];
import type { ContactRunDoc } from "@/services/clash/contactRuns";
import type { GroupMember, SavedGroup } from "@/utils/groups/savedGroups";

const RESULT_KEY = "_derived/clash/a/b/result.json";

function resultDoc(sourceKey: string, provenance: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    schema: "ada.clash/result@2",
    source_key: sourceKey,
    options: {},
    counts: { joints: 1 },
    joints: [
      {
        id: "j1",
        centre: [0, 0, 0],
        members: [
          { name: "a", kind: "BEAM", role: "incoming", end: "end" },
          { name: "b", kind: "BEAM", role: "landing" },
        ],
        type_key: "k",
        type_label: "K",
        applicable: [{ spec: "builtin.box_joint", priority: 50 }],
      },
    ],
    groups: [{ type_key: "k", type_label: "K", count: 1, joint_ids: ["j1"] }],
    provenance,
  };
}

const fileMemberOf = (key: string): GroupMember => ({ target: { kind: "file", source_key: key }, element: null, path: [] });
const nodeElement: GroupMember = {
  target: { kind: "node", provider: "prov", collection: "site", subject: "S1", revision: "r3", node: null },
  element: "beam-7",
  path: ["beam-7"],
};

function model(source: Model["source"]): Model {
  return {
    detail_id: "d1",
    created_at: "2026-11-19T10:15:00Z",
    status: "done",
    spec: "builtin.box_joint",
    source,
    label: "builtin.box_joint · x",
    glb_key: "_clash/details/d1/model.glb",
    stats_key: "_clash/details/d1/result.stats.json",
  };
}

function deps(over: Partial<Deps> = {}): Deps & { asked: string[] } {
  const asked: string[] = [];
  return {
    asked,
    readResult: async (_s, key) => (asked.push(`result ${key}`), resultDoc("models/frame.ifc")),
    readRun: async (_s, key) => {
      asked.push(`run ${key}`);
      throw new Error("no run");
    },
    findGroup: async (_s, by) => (asked.push(`group ${by.id ?? by.name}`), null),
    ...over,
  };
}

test("a check of a file: the file is the target and the one member, the result is the check's", async () => {
  const d = deps();
  const ctx = await detailContext(d, "user:me", model({ kind: "result", result_key: RESULT_KEY, source_key: "models/frame.ifc", target: { kind: "file" } }));
  assert.deepEqual(ctx.target, { kind: "file", sourceKey: "models/frame.ifc" });
  assert.deepEqual(ctx.members, [fileMemberOf("models/frame.ifc")]);
  assert.equal(ctx.derivedKey, RESULT_KEY);
  assert.equal(ctx.runKey, null);
  assert.equal(ctx.result.joints.length, 1);
  assert.deepEqual(ctx.notes, []);
  assert.deepEqual(d.asked, [`result ${RESULT_KEY}`]);
});

test("a check of a node: who publishes it comes from the result's provenance", async () => {
  const d = deps({
    readResult: async () =>
      resultDoc("site/S1", { provider: "prov", collection: "site", subject: "S1", revision: "r3" }),
  });
  const ctx = await detailContext(d, "user:me", model({ kind: "result", result_key: RESULT_KEY, target: { kind: "node", collection: "site", subject: "S1" } }));
  assert.equal(ctx.target.kind, "node");
  assert.deepEqual(ctx.members.map((m) => m.target), [
    { kind: "node", provider: "prov", collection: "site", subject: "S1", revision: "r3", node: null },
  ]);
  assert.deepEqual(ctx.notes, []);
});

test("a node whose provider is not recorded is put back on the panel but not loaded, and said", async () => {
  const d = deps({ readResult: async () => resultDoc("site/S1", { collection: "site", subject: "S1" }) });
  const ctx = await detailContext(d, "user:me", model({ kind: "result", result_key: RESULT_KEY, target: { kind: "node", collection: "site", subject: "S1" } }));
  assert.equal(ctx.target.kind, "node");
  assert.deepEqual(ctx.members, []);
  assert.match(ctx.notes[0] ?? "", /Sources/);
});

test("a check of a group: the members as the check resolved them", async () => {
  const d = deps({
    readResult: async () => resultDoc("group:tok", { group: { name: "deck", members: [fileMemberOf("models/a.ifc"), nodeElement] } }),
  });
  const ctx = await detailContext(d, "user:me", model({ kind: "result", result_key: RESULT_KEY, target: { kind: "group", name: "deck" } }));
  assert.equal(ctx.target.kind, "group");
  assert.equal(ctx.label, "group deck");
  assert.deepEqual(ctx.members, [fileMemberOf("models/a.ifc"), nodeElement]);
  assert.deepEqual(d.asked.filter((a) => a.startsWith("group")), [], "no saved group asked for");
});

test("a group check without members in its result takes the saved group of that name", async () => {
  const saved: SavedGroup = { id: "g7", name: "deck", created_at: "", members: [nodeElement] };
  const d = deps({ findGroup: async (_s, by) => (by.name === "deck" ? saved : null) });
  const ctx = await detailContext(d, "user:me", model({ kind: "result", result_key: RESULT_KEY, target: { kind: "group", name: "deck" } }));
  assert.deepEqual(ctx.members, [nodeElement]);
  assert.deepEqual(ctx.target, { kind: "group", name: "deck", members: [nodeElement], id: "g7" });
  assert.deepEqual(ctx.notes, []);
});

test("a group no longer saved: its check comes back, nothing is loaded, and that is said", async () => {
  const d = deps();
  const ctx = await detailContext(d, "user:me", model({ kind: "result", result_key: RESULT_KEY, target: { kind: "group", name: "gone" } }));
  assert.equal(ctx.target.kind, "group");
  assert.deepEqual(ctx.members, []);
  assert.match(ctx.notes[0] ?? "", /"gone" is no longer saved/);
  assert.equal(ctx.result.joints.length, 1, "the result is still the check's");
});

function run(target: ContactRunDoc["target"], over: Partial<ContactRunDoc> = {}): ContactRunDoc {
  return {
    schema: "ada.clash/contacts@1",
    run_id: "r1",
    created_at: "2026-11-19T12:00:00Z",
    engine: "wasm",
    checker: "convex-contact",
    target,
    geometry: { key: "_derived/site/S1/model.glb", sha256: "00", up_axis: "z" },
    options: { security_margin: 0.01, min_contact_area: 1e-6, line_contact_thickness: 1e-3 },
    members: [],
    contacts: [],
    provenance: {},
    ...over,
  } as ContactRunDoc;
}

test("a run on a group: the group it measured, filed under the run's key", async () => {
  const d = deps({
    readRun: async () => run({ kind: "group", group_id: "g1" }, { group: { id: "g1", name: "deck", members: [nodeElement] } }),
  });
  const ctx = await detailContext(d, "user:me", model({ kind: "run", run_key: "_clash/runs/r1.json", target: { kind: "group", group_id: "g1" } }));
  assert.deepEqual(ctx.target, { kind: "group", name: "deck", members: [nodeElement], id: "g1" });
  assert.equal(ctx.runKey, "_clash/runs/r1.json");
  assert.equal(ctx.derivedKey, null);
  assert.deepEqual(ctx.members, [nodeElement]);
});

test("a run on a file loads the file; a run on a node loads the GLB it measured", async () => {
  const onFile = await detailContext(
    deps({ readRun: async () => run({ kind: "file", source_key: "models/frame.ifc" }) }),
    "user:me",
    model({ kind: "run", run_key: "k" }),
  );
  assert.deepEqual(onFile.target, { kind: "file", sourceKey: "models/frame.ifc" });
  assert.deepEqual(onFile.members, [fileMemberOf("models/frame.ifc")]);

  const onNode = await detailContext(
    deps({ readRun: async () => run({ kind: "node", collection: "site", subject: "S1" }) }),
    "user:me",
    model({ kind: "run", run_key: "k" }),
  );
  assert.equal(onNode.target.kind, "node");
  assert.deepEqual(onNode.members, [fileMemberOf("_derived/site/S1/model.glb")]);
});

test("a source that names neither a result nor a run cannot be rebuilt", async () => {
  await assert.rejects(detailContext(deps(), "user:me", model({ kind: "result" })), /which clash check/);
});
