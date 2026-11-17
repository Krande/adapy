// A saved group's "Load into scene" puts the group's MEMBERS in the scene, not the documents they
// live in: an element of a source that delivers per node is loaded alone (its own row), a source
// that can only be delivered whole is loaded once and shows only its members, and a member loaded
// alone is found again by "Select".

import assert from "node:assert/strict";
import { test } from "node:test";

import { LoadCancelled } from "../../assets/delivery";
import type { TreeNodeData } from "../../components/tree_view/CustomNode";
import { runGroupLoad, type GroupLoadEnv } from "../../utils/groups/groupLoadRun";
import { groupSourceKey, planGroupLoads, rowsFromIndex, type MemberLoad } from "../../utils/groups/groupSources";
import {
  groupScenePartsOf,
  memberKey,
  resolveMembers,
  rowsOutside,
  type GroupMember,
  type GroupMemberTarget,
  type SavedGroup,
} from "../../utils/groups/savedGroups";
import { buildTreeIndices } from "../../utils/tree_view/treeGraph";

// One published subject `S`, a model of it loaded at `N`, three beams under one frame; a second
// subject `M` whose provider delivers one mesh; and a stored file.
const AT_N = { kind: "node", provider: "p", collection: "c", subject: "S", revision: "r1", node: "N" } as const;
const MESH = { kind: "node", provider: "p", collection: "c", subject: "M", revision: "r1", node: null } as const;
const FILE = { kind: "file", source_key: "proj/a.ifc" } as const;

const el = (target: GroupMemberTarget, element: string): GroupMember => ({ target, element, path: ["Frame", element] });
const B1 = el(AT_N, "B1");
const B2 = el(AT_N, "B2");
const B3 = el(AT_N, "B3");
const PLATE = el(MESH, "Plate");
const WALL = el(FILE, "Wall");
const ROWS = new Map([
  [memberKey(B1), "n1"],
  [memberKey(B2), "n2"],
  [memberKey(B3), "n3"],
]);

// --- the plan ----------------------------------------------------------------------------------

test("an element of a per-node source is loaded alone, at its own row", () => {
  const plan = planGroupLoads([B1, B2], ROWS, () => true);
  assert.deepEqual(plan.whole, []);
  assert.deepEqual(
    plan.alone.map((a) => [a.target.subject, a.target.node, a.label, a.members.length]),
    [
      ["S", "n1", "B1", 1],
      ["S", "n2", "B2", 1],
    ],
  );
});

test("a source that is one document is loaded once and shows only its members", () => {
  const plan = planGroupLoads([PLATE, el(MESH, "Plate 2"), WALL], new Map([[memberKey(PLATE), "m1"]]), () => false);
  assert.deepEqual(plan.alone, []);
  assert.deepEqual(
    plan.whole.map((w) => [w.key, w.only?.length]),
    [
      [groupSourceKey(PLATE), 2],
      [groupSourceKey(WALL), 1],
    ],
  );
});

test("an element the published tree does not place loads its source, showing only itself", () => {
  const plan = planGroupLoads([B1, el(AT_N, "Lost")], ROWS, () => true);
  assert.deepEqual(plan.alone.map((a) => a.target.node), ["n1"]);
  assert.deepEqual(plan.whole.map((w) => w.only?.map((m) => m.element)), [["Lost"]]);
});

test("a whole-model member loads the model with nothing hidden; two members of one row load it once", () => {
  const whole: GroupMember = { target: AT_N, element: null, path: [] };
  assert.deepEqual(planGroupLoads([B1, whole], ROWS, () => true), {
    alone: [],
    whole: [{ key: groupSourceKey(whole), target: AT_N, members: [B1, whole], only: null }],
  });
  const twin: GroupMember = { target: AT_N, element: "B1", path: ["Other", "B1"] };
  const plan = planGroupLoads([B1, twin], new Map([...ROWS, [memberKey(twin), "n1"]]), () => true);
  assert.equal(plan.alone.length, 1);
  assert.equal(plan.alone[0].members.length, 2);
  // A row that IS the source's own node is that source, not a model of its own.
  const self = planGroupLoads([B1], new Map([[memberKey(B1), "N"]]), () => true);
  assert.deepEqual([self.alone.length, self.whole.length, self.whole[0].only?.length], [0, 1, 1]);
});

test("the attribute index places an element only when exactly one node of its provider carries the name", () => {
  const rows = rowsFromIndex(
    [B1, B2, B3, WALL],
    [
      { node_id: "n1", full_name: "B1", provider: "p", providers: ["p"] },
      { node_id: "n2a", full_name: "B2" },
      { node_id: "n2b", full_name: "B2" },
      { node_id: "x3", full_name: "B3", provider: "other", providers: ["other"] },
      { node_id: "w", full_name: "Wall" },
    ],
  );
  assert.deepEqual([...rows], [[memberKey(B1), "n1"]]);
});

// --- the load ----------------------------------------------------------------------------------

const GROUP = (members: GroupMember[]): SavedGroup => ({ id: "g1", name: "con1", created_at: "", members });

/** An environment that records what it was asked for. `inScene` is what `select` finds loaded. */
function fakeEnv(over: Partial<GroupLoadEnv> = {}) {
  const calls: string[] = [];
  const inScene = new Set<string>();
  const env: GroupLoadEnv = {
    select: (group) => ({
      ranges: group.members.filter((m) => inScene.has(memberKey(m))).length,
      notLoaded: group.members.filter((m) => !inScene.has(memberKey(m))),
      notFound: [],
    }),
    deliversPerNode: async (t) => t.subject === "S",
    resolveRows: async (members) => {
      calls.push(`rows:${members.map((m) => m.element).join(",")}`);
      return ROWS;
    },
    startMember: (load: MemberLoad) => {
      calls.push(`start:${load.target.subject}#${load.target.node}`);
      return {
        ready: Promise.resolve(),
        finish: async () => {
          calls.push(`scene:${load.target.subject}#${load.target.node}`);
          for (const m of load.members) inScene.add(memberKey(m));
          return `assets:p/c/${load.target.subject}@r1#${load.target.node}`;
        },
      };
    },
    loadWhole: async (t) => {
      const name = t.kind === "file" ? t.source_key : `assets:p/c/${t.subject}@r1`;
      calls.push(`whole:${name}`);
      return name;
    },
    showOnly: async (name, members) => {
      calls.push(`only:${name}:${members.map((m) => m.element).join(",")}`);
      for (const m of members) inScene.add(memberKey(m));
      return 40;
    },
    settle: async (names) => void calls.push(`settle:${names.length}`),
    ...over,
  };
  return { env, calls, inScene };
}

test("a group's load asks for exactly its members, never the document they are in", async () => {
  const { env, calls } = fakeEnv();
  const out = await runGroupLoad(GROUP([B1, B2, B3]), env);
  assert.deepEqual(calls.filter((c) => c.startsWith("whole:") || c.startsWith("only:")), [], "the document is not loaded");
  assert.deepEqual(
    calls.filter((c) => c.startsWith("scene:")),
    ["scene:S#n1", "scene:S#n2", "scene:S#n3"],
    "one model per member, in group order",
  );
  assert.deepEqual([out.loaded, out.already, out.hidden, out.failed.length, out.selection.ranges], [3, 0, 0, 0, 3]);
});

test("members in several documents: each per-node member alone, each whole document once with the rest hidden", async () => {
  const { env, calls } = fakeEnv();
  const out = await runGroupLoad(GROUP([B1, PLATE, el(MESH, "Plate 2"), WALL, B2]), env);
  assert.deepEqual(calls, [
    "rows:B1,B2",
    "start:S#n1",
    "start:S#n2",
    "scene:S#n1",
    "scene:S#n2",
    "whole:assets:p/c/M@r1",
    "only:assets:p/c/M@r1:Plate,Plate 2",
    "whole:proj/a.ifc",
    "only:proj/a.ifc:Wall",
    "settle:4",
  ]);
  assert.deepEqual([out.loaded, out.hidden, out.failed.length, out.selection.ranges], [4, 80, 0, 5]);
});

test("a member that cannot be loaded is named, and the others still load", async () => {
  const base = fakeEnv();
  const { env, calls } = fakeEnv({
    startMember: (load) =>
      load.target.node === "n2"
        ? { ready: Promise.resolve(), finish: async () => Promise.reject(new Error("asset build error: no solid")) }
        : base.env.startMember(load),
    loadWhole: async (t) => {
      if (t.kind === "node") throw new Error("404 no published revision");
      return t.source_key;
    },
  });
  const out = await runGroupLoad(GROUP([B1, B2, B3, PLATE, WALL]), env);
  assert.deepEqual(
    out.failed.map((f) => [f.members.map((m) => m.element), f.reason]),
    [
      [["B2"], "asset build error: no solid"],
      [["Plate"], "404 no published revision"],
    ],
  );
  assert.equal(out.loaded, 3, "B1, B3 and the file");
  assert.ok(calls.includes("only:proj/a.ifc:Wall"));
});

test("what is already in the scene is not loaded again, and nothing loads when every member is there", async () => {
  const { env, calls, inScene } = fakeEnv();
  inScene.add(memberKey(B1));
  const out = await runGroupLoad(GROUP([B1, B2]), env);
  assert.deepEqual(calls.filter((c) => c.startsWith("scene:")), ["scene:S#n2"]);
  assert.equal(out.loaded, 1);
  inScene.add(memberKey(B2));
  calls.length = 0;
  assert.equal((await runGroupLoad(GROUP([B1, B2]), env)).loaded, 0);
  assert.deepEqual(calls, []);
});

test("a document that could not be reduced to its members is reported, not passed off as loaded right", async () => {
  const { env } = fakeEnv({ showOnly: async () => Promise.reject(new Error("its scene tree was not built in time")) });
  const out = await runGroupLoad(GROUP([WALL]), env);
  assert.equal(out.loaded, 1);
  assert.match(out.failed[0].reason, /shown whole: its scene tree was not built in time/);
});

test("builds run ahead a few at a time; a cancelled load stops the rest and finishes off what it started", async () => {
  const many = Array.from({ length: 6 }, (_, i) => el(AT_N, `E${i}`));
  const rows = new Map(many.map((m, i) => [memberKey(m), `e${i}`]));
  const started: string[] = [];
  const finished: string[] = [];
  const { env } = fakeEnv({
    resolveRows: async () => rows,
    startMember: (load) => {
      started.push(load.target.node as string);
      return {
        // Never ready: only the three started up front are ever in flight.
        ready: new Promise<void>(() => undefined),
        finish: async () => {
          finished.push(load.target.node as string);
          if (load.target.node === "e1") throw new LoadCancelled();
          return `assets:p/c/S@r1#${load.target.node}`;
        },
      };
    },
  });
  const run = runGroupLoad(GROUP(many), env);
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(started, ["e0", "e1", "e2"], "three in flight, the rest wait");
  const out = await run;
  assert.equal(out.loaded, 1);
  assert.deepEqual(out.failed, [], "a cancelled load is not a failure");
  assert.deepEqual(finished, ["e0", "e1", "e2"], "the one started ahead is finished off; none after it is started");
  assert.deepEqual(started, ["e0", "e1", "e2"]);
});

// --- finding a member that was loaded alone ------------------------------------------------------

function sceneOf(...roots: TreeNodeData[]) {
  const root: TreeNodeData = { id: "__roots__", name: "", children: roots };
  return buildTreeIndices(root);
}
const leaf = (id: string, name: string, model: string, range: string): TreeNodeData => ({
  id,
  name,
  children: [],
  model_key: model,
  node_name: `mesh_${model}`,
  rangeId: range,
});

test("a member loaded alone is in the scene: its model's root row carries its name", () => {
  // The model of row n1 alone; what its GLB calls its rows is the builder's business.
  const alone: TreeNodeData = {
    id: "r1",
    name: "B1",
    top_name: "B1",
    children: [leaf("r1-a", "solid 1", "k1", "0"), leaf("r1-b", "solid 2", "k1", "1")],
    model_key: "k1",
    source_name: "assets:p/c/S@r1#n1",
  };
  const other: TreeNodeData = { id: "r9", name: "B9", top_name: "B9", children: [leaf("r9-a", "x", "k9", "0")], model_key: "k9", source_name: "assets:p/c/S@r1#n9" };
  const idx = sceneOf(alone, other);
  const out = resolveMembers([B1, B2], idx, []);
  assert.deepEqual(out.rows.map((r) => r.id), ["r1"]);
  assert.deepEqual(out.notLoaded, [B2]);
  assert.deepEqual(out.notFound, []);
  // A run over the group measures the member's own model, and names it.
  const parts = groupScenePartsOf([B1], idx, []).parts;
  assert.equal(parts.length, 1);
  assert.equal(parts[0].target.kind === "node" && parts[0].target.node, "n1");
  assert.deepEqual([...parts[0].elements].sort(), ["0", "1"]);
});

test("a member is found in its subject loaded from higher up; another subject's model is not searched", () => {
  const whole: TreeNodeData = {
    id: "rw",
    name: "Site",
    top_name: "Site",
    children: [{ id: "area", name: "Area", model_key: "kw", children: [{ id: "frame", name: "Frame", model_key: "kw", children: [leaf("w-b1", "B1", "kw", "7")] }] }],
    model_key: "kw",
    source_name: "assets:p/c/S@r2",
  };
  const stranger: TreeNodeData = { id: "rs", name: "B2", top_name: "B2", children: [leaf("s-b2", "B2", "ks", "0")], model_key: "ks", source_name: "assets:p/c/T@r1#t1" };
  const out = resolveMembers([B1, B2], sceneOf(whole, stranger), []);
  assert.deepEqual(out.rows.map((r) => r.id), ["w-b1"]);
  assert.deepEqual(out.notLoaded, [B2]);
});

test("rowsOutside is every drawn row of the model that no kept row covers", () => {
  const frame: TreeNodeData = { id: "frame", name: "Frame", model_key: "k", children: [leaf("b1", "B1", "k", "0"), leaf("b2", "B2", "k", "1")] };
  const deck: TreeNodeData = { id: "deck", name: "Deck", model_key: "k", children: [leaf("p1", "P1", "k", "2")] };
  const root: TreeNodeData = { id: "r", name: "m", children: [frame, deck], model_key: "k", source_name: "proj/a.ifc" };
  assert.deepEqual(rowsOutside(root, [frame.children[0]]).map((r) => r.id).sort(), ["b2", "p1"]);
  assert.deepEqual(rowsOutside(root, [frame]).map((r) => r.id), ["p1"], "a kept level keeps what is under it");
  assert.deepEqual(rowsOutside(root, []).map((r) => r.id).sort(), ["b1", "b2", "p1"]);
  assert.deepEqual(rowsOutside(root, [root]), []);
});
