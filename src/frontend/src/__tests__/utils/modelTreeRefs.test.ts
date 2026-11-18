// A plugin's refs in the Model panel's tree: under the space they name, else under their
// structure, else under a trailing "All structures" row; in the flat view, under "Other". The
// tree's own rows keep their shape: a panel that draws no refs draws what it drew.

import assert from "node:assert/strict";
import { test } from "node:test";

import type { BuilderCell } from "@/state/cellbuilder/types";
import type { BuilderRef } from "@/state/cellbuilder/refsSlice";
import type { CellGroup } from "@/utils/cellbuilder/groups";
import { ALL_STRUCTURES_LABEL, OTHER_LABEL, buildModelTree, type GroupNode, type ModelNode } from "@/utils/cellbuilder/modelTree";

const box = (id: string, kind: BuilderCell["kind"], origin: [number, number, number], extra: Partial<BuilderCell> = {}): BuilderCell => ({
  id,
  name: id,
  kind,
  origin,
  size: [4, 4, 3],
  params: {},
  ...extra,
});

const groups: CellGroup[] = [
  { name: "Upper", blueprint: "plate" },
  { name: "Lower", blueprint: "frame" },
];
const cells: Record<string, BuilderCell> = Object.fromEntries(
  [
    box("room", "cell", [0, 0, 0], { group: "Upper" }),
    box("hall", "cell", [0, 0, -3], { group: "Lower" }),
    box("pump", "equipment", [1, 1, 0], { size: [1, 1, 1], params: { SPACE_NAME: "room" } }),
  ].map((c) => [c.id, c]),
);

const ref = (key: string, extra: Partial<BuilderRef> = {}): BuilderRef => ({
  id: `p:${key}`,
  owner: "p",
  key,
  name: key,
  kindLabel: "Marker",
  origin: [0, 0, 0],
  size: [1, 1, 1],
  include: true,
  look: "box",
  edit: { move: false, resize: false, rename: false, remove: false },
  ...extra,
});

const refs: BuilderRef[] = [
  ref("inRoom", { space: "room", structure: "Upper" }),
  ref("onLower", { structure: "Lower" }),
  ref("everywhere", { structure: "*" }),
  ref("unknown", { structure: "Gone" }),
  ref("spaceWins", { space: "hall", structure: "Upper" }),
  ref("noSuchSpace", { space: "nowhere", structure: "Upper", include: false }),
];

const find = (nodes: readonly ModelNode[], pred: (g: GroupNode) => boolean): GroupNode | undefined => {
  for (const n of nodes) {
    if (n.type !== "group") continue;
    if (pred(n)) return n;
    const hit = find(n.children, pred);
    if (hit) return hit;
  }
  return undefined;
};
const refKeys = (g: GroupNode | undefined) => (g?.refs ?? []).map((r) => r.ref.key);

test("tree: a ref goes under its space, else its structure, else All structures", () => {
  const nodes = buildModelTree("tree", { cells, groups, refs });
  const room = find(nodes, (g) => g.kind === "cell" && g.cell?.name === "room");
  const hall = find(nodes, (g) => g.kind === "cell" && g.cell?.name === "hall");
  const upper = find(nodes, (g) => g.kind === "structure" && g.label === "Upper");
  const lower = find(nodes, (g) => g.kind === "structure" && g.label === "Lower");
  assert.deepEqual(refKeys(room), ["inRoom"]);
  assert.deepEqual(refKeys(hall), ["spaceWins"], "a named space wins over the structure");
  assert.deepEqual(refKeys(lower), ["onLower"]);
  assert.deepEqual(refKeys(upper), ["noSuchSpace"], "a space the model lacks falls back to the structure");
  assert.equal(room!.refs![0].depth, room!.depth + 1);
  const last = nodes[nodes.length - 1];
  assert.ok(last.type === "group" && last.kind === "all");
  assert.equal(last.label, ALL_STRUCTURES_LABEL);
  assert.deepEqual(refKeys(last), ["everywhere", "unknown"]);
  assert.deepEqual(last.itemIds, [], "a ref is no component: no ids to select or cascade");
  assert.equal(last.include, "on");
});

test("the rows the tree had keep their shape and contents", () => {
  const without = buildModelTree("tree", { cells, groups });
  const withRefs = buildModelTree("tree", { cells, groups, refs: refs.slice(0, 2) });
  const strip = (nodes: readonly ModelNode[]): unknown =>
    nodes.map((n) => (n.type === "group" ? { ...n, refs: undefined, children: strip(n.children) } : n));
  assert.deepEqual(strip(withRefs), strip(without));
  assert.equal(find(without, () => true)?.refs, undefined, "no refs, no field");
  assert.equal(withRefs.length, without.length, "no trailing row when every ref found a place");
});

test("no structure layer: a space still takes its refs; the rest go to All structures", () => {
  const plain = { room: { ...cells.room, group: undefined } };
  const nodes = buildModelTree("tree", { cells: plain, groups: [], refs: [ref("a", { space: "room" }), ref("b", { structure: "Upper" })] });
  assert.deepEqual(refKeys(find(nodes, (g) => g.kind === "cell")), ["a"]);
  assert.deepEqual(refKeys(find(nodes, (g) => g.kind === "all")), ["b"]);
});

test("flat: every ref under a trailing Other row, its mark the refs' includes", () => {
  const nodes = buildModelTree("flat", { cells, groups, refs });
  const last = nodes[nodes.length - 1];
  assert.ok(last.type === "group" && last.kind === "other");
  assert.equal(last.label, OTHER_LABEL);
  assert.equal(last.refs!.length, refs.length);
  assert.equal(last.include, "mixed");
  assert.equal(nodes.filter((n) => n.type === "item").length, Object.keys(cells).length);
});
