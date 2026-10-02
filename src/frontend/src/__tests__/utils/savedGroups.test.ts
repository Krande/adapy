// Saved groups: the stored document (parse, serialise, read-modify-write merge), capturing a
// selection as collapsed members across models, finding members again in a reloaded scene, the
// clash wire body, and which members a provider can be asked for.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { TreeNodeData } from "../../components/tree_view/CustomNode";
import {
  applyGroupsChange,
  collapseSelectedRows,
  EMPTY_GROUPS_DOC,
  geometryRowsUnder,
  groupNodePlan,
  GROUPS_SCHEMA,
  GroupsDocError,
  memberKey,
  membersForRows,
  newGroupId,
  parseGroupsDoc,
  resolveMembers,
  serialiseGroupsDoc,
  targetForSource,
  type GroupMember,
  type SavedGroup,
} from "../../utils/groups/savedGroups";
import { buildTreeIndices } from "../../utils/tree_view/treeGraph";

// --- a two-model scene ---------------------------------------------------------------------

let nextRange = 0;
function leaf(id: string, name: string, model: string): TreeNodeData {
  return { id, name, children: [], model_key: model, node_name: `mesh_${model}`, rangeId: String(nextRange++) };
}
function level(id: string, name: string, model: string, children: TreeNodeData[]): TreeNodeData {
  return { id, name, children, model_key: model };
}

/** File model `a.ifc`: Site > Deck A {P1, P2}, Deck B {P1}. Asset model: Area > Pipe {S1, S2}. */
function scene() {
  nextRange = 0;
  const deckA = level("a-deckA", "Deck A", "kA", [leaf("a-p1", "P1", "kA"), leaf("a-p2", "P2", "kA")]);
  const deckB = level("a-deckB", "Deck B", "kA", [leaf("a-b-p1", "P1", "kA")]);
  const site = level("a-site", "Site", "kA", [deckA, deckB]);
  const rootA: TreeNodeData = { id: "rootA", name: "a.ifc", children: [site], model_key: "kA", source_name: "proj/a.ifc" };
  const pipe = level("b-pipe", "Pipe", "kB", [leaf("b-s1", "S1", "kB"), leaf("b-s2", "S2", "kB")]);
  const area = level("b-area", "Area", "kB", [pipe]);
  const rootB: TreeNodeData = {
    id: "rootB",
    name: "Area",
    children: [area],
    model_key: "kB",
    source_name: "assets:prov/plant/S10@r1#N20",
  };
  const root: TreeNodeData = { id: "__roots__", name: "", children: [rootA, rootB] };
  return { root, idx: buildTreeIndices(root) };
}

const FILE_A = { kind: "file", source_key: "proj/a.ifc" } as const;
const NODE_B = { kind: "node", provider: "prov", collection: "plant", subject: "S10", revision: "r1", node: "N20" } as const;

// --- document --------------------------------------------------------------------------------

test("an empty or missing document reads as no groups", () => {
  assert.deepEqual(parseGroupsDoc(""), EMPTY_GROUPS_DOC);
  assert.deepEqual(parseGroupsDoc(null), EMPTY_GROUPS_DOC);
});

test("an unknown schema is refused, never read and written back over", () => {
  assert.throws(() => parseGroupsDoc(JSON.stringify({ schema: "ada.viewer/groups@2", groups: [] })), GroupsDocError);
  assert.throws(() => parseGroupsDoc("{not json"), GroupsDocError);
});

test("malformed groups and members are dropped, valid ones kept, ids de-duplicated", () => {
  const doc = parseGroupsDoc(
    JSON.stringify({
      schema: GROUPS_SCHEMA,
      groups: [
        {
          id: "g1",
          name: " Decks ",
          created_at: "t",
          members: [
            { target: FILE_A, element: "Deck A", path: ["Site", "Deck A"] },
            { target: { kind: "file" }, element: null, path: [] },
            { target: { kind: "bucket", key: "x" }, element: null, path: [] },
            { target: NODE_B, element: null, path: [] },
          ],
        },
        { id: "g1", name: "duplicate id", members: [] },
        { id: "", name: "no id", members: [] },
        { id: "g2", name: "", members: [] },
      ],
    }),
  );
  assert.equal(doc.groups.length, 1);
  assert.equal(doc.groups[0].name, "Decks");
  assert.deepEqual(doc.groups[0].members, [
    { target: FILE_A, element: "Deck A", path: ["Site", "Deck A"] },
    { target: NODE_B, element: null, path: [] },
  ]);
});

test("serialise round-trips through parse", () => {
  const group: SavedGroup = {
    id: "g1",
    name: "Pipes",
    created_at: "2026-10-02T00:00:00Z",
    created_by: "someone",
    members: [{ target: NODE_B, element: "S1", path: ["Area", "Pipe", "S1"] }],
  };
  const doc = applyGroupsChange(EMPTY_GROUPS_DOC, { kind: "put", group });
  assert.deepEqual(parseGroupsDoc(serialiseGroupsDoc(doc)), doc);
  assert.equal(JSON.parse(serialiseGroupsDoc(doc)).schema, GROUPS_SCHEMA);
});

test("a change applies to the LATEST document, keeping groups others wrote meanwhile", () => {
  const mine: SavedGroup = { id: "mine", name: "Mine", created_at: "t1", members: [] };
  const theirs: SavedGroup = { id: "theirs", name: "Theirs", created_at: "t2", members: [] };
  // What this viewer read earlier had only `mine`; the store now also holds `theirs`.
  const latest = { schema: GROUPS_SCHEMA, groups: [mine, theirs] } as const;
  const renamed = applyGroupsChange(latest, { kind: "rename", id: "mine", name: "Mine v2", at: "t3" });
  assert.deepEqual(
    renamed.groups.map((g) => [g.id, g.name]),
    [
      ["mine", "Mine v2"],
      ["theirs", "Theirs"],
    ],
  );
  assert.equal(renamed.groups[0].updated_at, "t3");
  const deleted = applyGroupsChange(renamed, { kind: "delete", id: "mine" });
  assert.deepEqual(deleted.groups.map((g) => g.id), ["theirs"]);
  // A rename of a group someone else deleted does not bring it back.
  assert.deepEqual(applyGroupsChange(deleted, { kind: "rename", id: "mine", name: "x", at: "t4" }).groups.map((g) => g.id), ["theirs"]);
  // Last write wins per group: a put replaces the group with the same id in place.
  const replaced = applyGroupsChange(deleted, { kind: "put", group: { ...theirs, name: "Theirs v2" } });
  assert.deepEqual(replaced.groups.map((g) => g.name), ["Theirs v2"]);
});

test("group ids are distinct for distinct randomness and time-ordered", () => {
  assert.notEqual(newGroupId(1000, () => 0.1), newGroupId(1000, () => 0.2));
  assert.ok(newGroupId(1000, () => 0) < newGroupId(2000, () => 0));
});

// --- capture ----------------------------------------------------------------------------------

test("a level picked in the tree is stored as the level, not as its leaves", () => {
  const { idx } = scene();
  const rows = collapseSelectedRows(new Set(["a-p1", "a-p2"]), idx);
  assert.deepEqual(rows.map((r) => r.id), ["a-deckA"]);
  const { members } = membersForRows(rows, idx, []);
  assert.deepEqual(members, [{ target: FILE_A, element: "Deck A", path: ["Site", "Deck A"] }]);
});

test("a partial selection keeps the picked rows; a mixed selection spans models", () => {
  const { idx } = scene();
  const rows = collapseSelectedRows(new Set(["a-p1", "a-b-p1", "b-s1", "b-s2"]), idx);
  // a-b-p1 is all of Deck B; b-s1 + b-s2 are all of model B, so the whole model.
  assert.deepEqual(rows.map((r) => r.id), ["a-p1", "a-deckB", "rootB"]);
  const { members, skipped } = membersForRows(rows, idx, []);
  assert.deepEqual(skipped, []);
  assert.deepEqual(members, [
    { target: FILE_A, element: "P1", path: ["Site", "Deck A", "P1"] },
    { target: FILE_A, element: "Deck B", path: ["Site", "Deck B"] },
    { target: NODE_B, element: null, path: [] },
  ]);
});

test("every leaf of a model selected collapses to the whole model", () => {
  const { idx } = scene();
  const rows = collapseSelectedRows(new Set(["a-p1", "a-p2", "a-b-p1"]), idx);
  assert.deepEqual(rows.map((r) => r.id), ["rootA"]);
  assert.deepEqual(membersForRows(rows, idx, []).members, [{ target: FILE_A, element: null, path: [] }]);
});

test("a model not loaded from storage is skipped with a reason", () => {
  const { root } = scene();
  const orphan: TreeNodeData = { id: "rootC", name: "scratch", children: [leaf("c-x", "X", "kC")], model_key: "kC" };
  const withOrphan: TreeNodeData = { ...root, children: [...root.children, orphan] };
  const idx = buildTreeIndices(withOrphan);
  const { members, skipped } = membersForRows(collapseSelectedRows(new Set(["c-x"]), idx), idx, []);
  assert.deepEqual(members, []);
  assert.equal(skipped.length, 1);
});

test("the asset browser's record of a load wins over parsing the source name", () => {
  const ref = { provider: "p2", collection: "c2", subject: "s2", revision: "r9" };
  assert.deepEqual(targetForSource("assets:prov/plant/S10@r1#N20", [{ sourceName: "assets:prov/plant/S10@r1#N20", ref }]), {
    kind: "node",
    provider: "p2",
    collection: "c2",
    subject: "s2",
    revision: "r9",
    node: null,
  });
  assert.deepEqual(targetForSource("assets:prov/plant/S10@r1", []), { ...NODE_B, node: null });
  assert.equal(targetForSource("assets:garbage", []), null);
});

// --- restore ----------------------------------------------------------------------------------

test("members are found again by name and path across models, after ids changed", () => {
  const { idx } = scene(); // a fresh build: new range ids, same names
  const members: GroupMember[] = [
    { target: FILE_A, element: "P1", path: ["Site", "Deck B", "P1"] },
    { target: NODE_B, element: "Pipe", path: ["Area", "Pipe"] },
  ];
  const { rows, notLoaded, notFound } = resolveMembers(members, idx, []);
  assert.deepEqual(rows.map((r) => r.id), ["a-b-p1", "b-pipe"]);
  assert.deepEqual(notLoaded, []);
  assert.deepEqual(notFound, []);
  assert.deepEqual(geometryRowsUnder(rows).map((r) => r.id).sort(), ["a-b-p1", "b-s1", "b-s2"]);
});

test("the path tells same-named rows apart; a looser path matches when the model loads from elsewhere", () => {
  const { idx } = scene();
  const exact = resolveMembers([{ target: FILE_A, element: "P1", path: ["Site", "Deck A", "P1"] }], idx, []);
  assert.deepEqual(exact.rows.map((r) => r.id), ["a-p1"]);
  // Stored from a load one level lower (no "Site" above): still found, by the path's tail.
  const lower = resolveMembers([{ target: FILE_A, element: "P1", path: ["Deck A", "P1"] }], idx, []);
  assert.deepEqual(lower.rows.map((r) => r.id), ["a-p1"]);
});

test("a member whose model is not loaded, or whose row is gone, is reported", () => {
  const { idx } = scene();
  const out = resolveMembers(
    [
      { target: { kind: "file", source_key: "proj/other.ifc" }, element: null, path: [] },
      { target: FILE_A, element: "Deck Z", path: ["Site", "Deck Z"] },
      { target: { ...NODE_B, revision: "r2" }, element: null, path: [] },
    ],
    idx,
    [],
  );
  assert.equal(out.notLoaded.length, 1);
  assert.equal(out.notFound.length, 1);
  // Another revision of the same node still matches.
  assert.deepEqual(out.rows.map((r) => r.id), ["rootB"]);
});

// --- the wire shape -------------------------------------------------------------------------

test("a member is exactly {target, element, path} with a snake_case target", () => {
  const { idx } = scene();
  const { members } = membersForRows(collapseSelectedRows(new Set(["a-p1", "b-s1"]), idx), idx, []);
  for (const m of members) assert.deepEqual(Object.keys(m).sort(), ["element", "path", "target"]);
  assert.deepEqual(Object.keys(members[0].target).sort(), ["kind", "source_key"]);
  assert.deepEqual(Object.keys(members[1].target).sort(), ["collection", "kind", "node", "provider", "revision", "subject"]);
});

// --- request geometry -------------------------------------------------------------------------

test("whole nodes request their own id, elements need a resolved row, files cannot be requested", () => {
  const sub: GroupMember = { target: { ...NODE_B, node: null }, element: null, path: [] };
  const whole: GroupMember = { target: NODE_B, element: null, path: [] };
  const inside: GroupMember = { target: NODE_B, element: "S1", path: ["Area", "Pipe", "S1"] };
  const lost: GroupMember = { target: NODE_B, element: "S9", path: ["Area", "Pipe", "S9"] };
  const file: GroupMember = { target: FILE_A, element: null, path: [] };
  const plan = groupNodePlan([sub, whole, whole, inside, lost, file], new Map([[memberKey(inside), "row-S1"]]));
  assert.deepEqual(plan.byCollection.get("plant"), [
    { collection: "plant", id: "S10" },
    { collection: "plant", id: "N20" },
    { collection: "plant", id: "row-S1", label: "S1" },
  ]);
  assert.deepEqual(plan.unresolved.map((u) => u.member), [lost, file]);
});
