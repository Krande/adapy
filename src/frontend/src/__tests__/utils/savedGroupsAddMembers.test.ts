// Saved groups, "Add to group…": rows picked in the Scene menu become members folded the way a
// selection is, and adding members to a stored group appends only the new ones.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { TreeNodeData } from "../../components/tree_view/CustomNode";
import {
  addMembersToGroup,
  applyGroupsChange,
  EMPTY_GROUPS_DOC,
  memberKey,
  membersForPickedRows,
  newGroupId,
  selectionGroupId,
  type GroupMember,
  type SavedGroup,
} from "../../utils/groups/savedGroups";
import { buildTreeIndices } from "../../utils/tree_view/treeGraph";

let nextRange = 0;
function leaf(id: string, name: string): TreeNodeData {
  return { id, name, children: [], model_key: "kA", node_name: "mesh_kA", rangeId: String(nextRange++) };
}

/** `a.ifc`: Site > Deck A {P1, P2}, Deck B {P1}; plus a model not loaded from storage. */
function scene() {
  nextRange = 0;
  const deckA: TreeNodeData = { id: "deckA", name: "Deck A", children: [leaf("a-p1", "P1"), leaf("a-p2", "P2")], model_key: "kA" };
  const deckB: TreeNodeData = { id: "deckB", name: "Deck B", children: [leaf("b-p1", "P1")], model_key: "kA" };
  const site: TreeNodeData = { id: "site", name: "Site", children: [deckA, deckB], model_key: "kA" };
  const rootA: TreeNodeData = { id: "rootA", name: "a.ifc", children: [site], model_key: "kA", source_name: "proj/a.ifc" };
  const loose: TreeNodeData = {
    id: "rootX",
    name: "dropped.glb",
    children: [{ id: "x1", name: "X1", children: [], model_key: "kX", node_name: "mesh_kX", rangeId: "0" }],
    model_key: "kX",
  };
  const root: TreeNodeData = { id: "__roots__", name: "", children: [rootA, loose] };
  return { root, idx: buildTreeIndices(root), deckA, deckB, site, rootA, loose };
}

const FILE_A = { kind: "file", source_key: "proj/a.ifc" } as const;
const m = (element: string | null, path: string[] = []): GroupMember => ({ target: FILE_A, element, path });

test("picked rows fold up to whole levels: both plates of a deck are the deck", () => {
  const s = scene();
  const p1 = s.idx.byId.get("a-p1")!;
  const p2 = s.idx.byId.get("a-p2")!;
  const { members, skipped } = membersForPickedRows([p1, p2], s.idx, []);
  assert.deepEqual(members, [m("Deck A", ["Site", "Deck A"])]);
  assert.deepEqual(skipped, []);
});

test("a picked level stands for its subtree, and a picked model root for the whole model", () => {
  const s = scene();
  assert.deepEqual(membersForPickedRows([s.deckB], s.idx, []).members, [m("Deck B", ["Site", "Deck B"])]);
  assert.deepEqual(membersForPickedRows([s.rootA], s.idx, []).members, [m(null)]);
  // A level and a row inside it: one member, the level.
  const inside = s.idx.byId.get("b-p1")!;
  assert.deepEqual(membersForPickedRows([s.deckB, inside], s.idx, []).members, [m("Deck B", ["Site", "Deck B"])]);
});

test("rows of a model not loaded from storage are skipped with a reason, nothing drawn gives nothing", () => {
  const s = scene();
  const out = membersForPickedRows([s.loose, s.deckA], s.idx, []);
  assert.deepEqual(out.members, [m("Deck A", ["Site", "Deck A"])]);
  assert.equal(out.skipped.length, 1);
  assert.match(out.skipped[0].reason, /not loaded from storage/);
  const empty: TreeNodeData = { id: "e", name: "Empty", children: [] };
  assert.deepEqual(membersForPickedRows([empty], s.idx, []), { members: [], skipped: [] });
});

const base: SavedGroup = {
  id: "g1",
  name: "Decks",
  created_at: "t0",
  created_by: "someone",
  members: [m("Deck A", ["Site", "Deck A"])],
};

test("adding appends only members the group lacks, in order, and stamps updated_at", () => {
  const add = [m("Deck A", ["Site", "Deck A"]), m("Deck B", ["Site", "Deck B"]), m("Deck B", ["Site", "Deck B"]), m(null)];
  const { group, added } = addMembersToGroup(base, add, "t1");
  assert.equal(added, 2);
  assert.deepEqual(group.members, [m("Deck A", ["Site", "Deck A"]), m("Deck B", ["Site", "Deck B"]), m(null)]);
  assert.equal(group.updated_at, "t1");
  // Identity is kept: id, name, creation.
  assert.equal(group.id, "g1");
  assert.equal(group.name, "Decks");
  assert.equal(group.created_at, "t0");
  assert.equal(group.created_by, "someone");
  assert.equal(new Set(group.members.map(memberKey)).size, group.members.length);
  // The input group is not mutated.
  assert.equal(base.members.length, 1);
  assert.equal(base.updated_at, undefined);
});

test("same element name under a different path is a different member", () => {
  const { added } = addMembersToGroup(base, [m("Deck A", ["Other", "Deck A"])], "t1");
  assert.equal(added, 1);
});

test("adding only members already held changes nothing -- same object, no updated_at", () => {
  const out = addMembersToGroup(base, [m("Deck A", ["Site", "Deck A"])], "t1");
  assert.equal(out.added, 0);
  assert.equal(out.group, base);
  assert.deepEqual(addMembersToGroup(base, [], "t1"), { group: base, added: 0 });
});

test("the merged group put into the latest document replaces that group only", () => {
  const theirs: SavedGroup = { id: "g2", name: "Theirs", created_at: "t", members: [] };
  let doc = applyGroupsChange(EMPTY_GROUPS_DOC, { kind: "put", group: base });
  doc = applyGroupsChange(doc, { kind: "put", group: theirs });
  const { group } = addMembersToGroup(doc.groups[0], [m(null)], "t2");
  const next = applyGroupsChange(doc, { kind: "put", group });
  assert.deepEqual(next.groups.map((g) => g.id), ["g1", "g2"]);
  assert.equal(next.groups[0].members.length, 2);
  assert.equal(next.groups[1], theirs);
});

test("a selection is checked under an id of its own: order-free, distinct, never a saved group's", () => {
  const a: GroupMember = { target: { kind: "file", source_key: "proj/a.ifc" }, element: "Deck A", path: ["Site"] };
  const b: GroupMember = { target: { kind: "file", source_key: "proj/a.ifc" }, element: "Deck B", path: ["Site"] };
  const id = selectionGroupId([a, b]);
  assert.match(id, /^selection-[0-9a-f]{16}$/);
  assert.equal(selectionGroupId([b, a]), id, "the same members in another order");
  assert.equal(selectionGroupId([a, b, a]), id, "a repeated member");
  assert.notEqual(selectionGroupId([a]), id);
  assert.ok(!newGroupId().startsWith("selection-"));
});