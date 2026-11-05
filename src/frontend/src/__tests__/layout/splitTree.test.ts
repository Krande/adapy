// The split grid: docking beside a node or into a group, removing, tab moves,
// resizing, the grid as drawn, the keyboard's geometry, and reading a stored grid.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DEFAULT_GRID,
  ROOT_NODE,
  VIEWPORT_NODE,
  dockInGrid,
  equaliseSplit,
  findNode,
  gridGroups,
  gridPanels,
  groupOf,
  neighbourRect,
  normaliseGrid,
  removeFromGrid,
  resizePair,
  setActiveTab,
  setSplitSizes,
  shownGrid,
  splitShare,
  type GridNode,
  type SplitNode,
} from "@/layout/splitTree";

const VP = VIEWPORT_NODE;
const close = (a: number, b: number) => Math.abs(a - b) < 1e-9;
const sizesClose = (got: number[], want: number[], msg = "") =>
  assert.ok(got.length === want.length && got.every((v, i) => close(v, want[i])), `${got} != ${want} ${msg}`);

/** The tree's shape as a string: splits as `row(..)` / `col(..)`, groups as `[a,b]`, the viewport as `V`. */
function shape(n: GridNode): string {
  if (n.kind === "viewport") return "V";
  if (n.kind === "group") return `[${n.panels.join(",")}]`;
  return `${n.dir}(${n.children.map(shape).join(" ")})`;
}

function sumsToOne(n: GridNode): void {
  if (n.kind !== "split") return;
  assert.ok(close(n.sizes.reduce((a, b) => a + b, 0), 1), `sizes of ${n.id} sum to 1`);
  assert.equal(n.sizes.length, n.children.length);
  assert.ok(n.children.length >= 2, "no split of one child");
  for (const c of n.children) {
    assert.ok(!(c.kind === "split" && c.dir === n.dir), "no split in a split of its direction");
    sumsToOne(c);
  }
}

test("the default grid is the viewport alone", () => {
  assert.deepEqual(DEFAULT_GRID, { kind: "viewport" });
  assert.deepEqual(gridGroups(DEFAULT_GRID), []);
});

test("docking at the viewport's edges splits it, the new group taking its share", () => {
  let g = dockInGrid(DEFAULT_GRID, "a", { node: VP, edge: "right" });
  assert.equal(shape(g), "row(V [a])");
  sizesClose((g as SplitNode).sizes, [1 - splitShare(VP), splitShare(VP)]);
  g = dockInGrid(g, "b", { node: VP, edge: "left" });
  assert.equal(shape(g), "row([b] V [a])", "a split of the same direction gains a sibling");
  sizesClose((g as SplitNode).sizes, [0.7 * 0.3, 0.7 * 0.7, 0.3]);
  g = dockInGrid(g, "c", { node: VP, edge: "bottom" });
  assert.equal(shape(g), "row([b] col(V [c]) [a])", "across, the target is wrapped");
  g = dockInGrid(g, "d", { node: VP, edge: "top" });
  assert.equal(shape(g), "row([b] col([d] V [c]) [a])");
  sumsToOne(g);
  assert.deepEqual(gridPanels(g), ["b", "d", "c", "a"]);
  assert.equal(new Set(gridGroups(g).map((x) => x.id)).size, 4, "group ids are unique");
});

test("docking at a group's edge splits the group half and half", () => {
  let g = dockInGrid(DEFAULT_GRID, "a", { node: VP, edge: "right" });
  const ga = groupOf(g, "a")!.id;
  g = dockInGrid(g, "b", { node: ga, edge: "bottom" });
  assert.equal(shape(g), "row(V col([a] [b]))");
  const col = (g as SplitNode).children[1] as SplitNode;
  sizesClose(col.sizes, [0.5, 0.5]);
  g = dockInGrid(g, "c", { node: ga, edge: "top" });
  assert.equal(shape(g), "row(V col([c] [a] [b]))");
  sizesClose(((g as SplitNode).children[1] as SplitNode).sizes, [0.25, 0.25, 0.5]);
  sumsToOne(g);
});

test("docking at the grid's own edge goes outside everything", () => {
  let g = dockInGrid(DEFAULT_GRID, "a", { node: VP, edge: "right" });
  g = dockInGrid(g, "b", { node: ROOT_NODE, edge: "right" });
  assert.equal(shape(g), "row(V [a] [b])");
  sizesClose((g as SplitNode).sizes, [0.7 * 0.75, 0.3 * 0.75, 0.25]);
  g = dockInGrid(g, "c", { node: ROOT_NODE, edge: "bottom" });
  assert.equal(shape(g), "col(row(V [a] [b]) [c])");
  sumsToOne(g);
});

test("docking at a group's center stacks the panel as its shown tab, at the index", () => {
  let g = dockInGrid(DEFAULT_GRID, "a", { node: VP, edge: "right" });
  const ga = groupOf(g, "a")!.id;
  g = dockInGrid(g, "b", { node: ga, edge: "center" });
  assert.equal(shape(g), "row(V [a,b])");
  assert.equal(groupOf(g, "b")!.active, "b");
  g = dockInGrid(g, "c", { node: ga, edge: "center", index: 0 });
  assert.equal(shape(g), "row(V [c,a,b])");
  // Moving a tab within its group: before the panel at the index, itself not counted.
  g = dockInGrid(g, "c", { node: ga, edge: "center", index: 3 });
  assert.equal(shape(g), "row(V [a,b,c])");
  g = dockInGrid(g, "c", { node: ga, edge: "center", index: 1 });
  assert.equal(shape(g), "row(V [a,c,b])");
  const same = dockInGrid(g, "c", { node: ga, edge: "center", index: 1 });
  assert.equal(same, g, "a tab dropped where it is is no change");
  assert.equal(dockInGrid(g, "x", { node: VP, edge: "center" }), g, "the viewport takes no tabs");
  assert.equal(dockInGrid(g, "x", { node: "nope", edge: "left" }), g, "nor does a node the grid does not have");
});

test("moving a tab between groups, and to an edge, collapses what it leaves", () => {
  let g = dockInGrid(DEFAULT_GRID, "a", { node: VP, edge: "right" });
  const ga = groupOf(g, "a")!.id;
  g = dockInGrid(g, "b", { node: ga, edge: "center" });
  g = dockInGrid(g, "c", { node: VP, edge: "left" });
  assert.equal(shape(g), "row([c] V [a,b])");
  // A tab dragged to its own group's bottom edge: the group splits.
  g = dockInGrid(g, "b", { node: ga, edge: "bottom" });
  assert.equal(shape(g), "row([c] V col([a] [b]))");
  // The last panel of a group moved out: the group goes, its split collapses.
  g = dockInGrid(g, "b", { node: groupOf(g, "c")!.id, edge: "center" });
  assert.equal(shape(g), "row([c,b] V [a])");
  sumsToOne(g);
  // A panel alone in its group dropped on its own group: no change.
  assert.equal(dockInGrid(g, "a", { node: ga, edge: "left" }), g);
  assert.equal(dockInGrid(g, "a", { node: ga, edge: "center" }), g);
});

test("removing a panel: the next tab shows, an empty group goes, a one-child split becomes its child", () => {
  let g = dockInGrid(DEFAULT_GRID, "a", { node: VP, edge: "right" });
  const ga = groupOf(g, "a")!.id;
  g = dockInGrid(g, "b", { node: ga, edge: "center" });
  g = dockInGrid(g, "c", { node: ga, edge: "bottom" });
  g = setActiveTab(g, "a");
  assert.equal(shape(g), "row(V col([a,b] [c]))");
  g = removeFromGrid(g, "a");
  assert.equal(groupOf(g, "b")!.active, "b");
  g = removeFromGrid(g, "c");
  assert.equal(shape(g), "row(V [b])", "the column of one collapsed into the row");
  g = removeFromGrid(g, "b");
  assert.deepEqual(g, DEFAULT_GRID);
  assert.equal(removeFromGrid(g, "zz"), g, "a panel not in the grid: no change");
});

test("a collapsing split of the parent's direction merges into the parent, sizes kept", () => {
  let g = dockInGrid(DEFAULT_GRID, "a", { node: VP, edge: "right" }); // row(V a)
  g = dockInGrid(g, "b", { node: groupOf(g, "a")!.id, edge: "bottom" }); // row(V col(a b))
  g = dockInGrid(g, "c", { node: groupOf(g, "a")!.id, edge: "right" }); // row(V col(row(a c) b))
  assert.equal(shape(g), "row(V col(row([a] [c]) [b]))");
  g = removeFromGrid(g, "b");
  assert.equal(shape(g), "row(V [a] [c])", "row(a c) flattened into the root row");
  sizesClose((g as SplitNode).sizes, [0.7, 0.15, 0.15]);
});

test("setting the shown tab", () => {
  let g = dockInGrid(DEFAULT_GRID, "a", { node: VP, edge: "right" });
  g = dockInGrid(g, "b", { node: groupOf(g, "a")!.id, edge: "center" });
  assert.equal(groupOf(g, "b")!.active, "b");
  const h = setActiveTab(g, "a");
  assert.equal(groupOf(h, "a")!.active, "a");
  assert.equal(setActiveTab(h, "a"), h);
  assert.equal(setActiveTab(h, "zz"), h);
});

test("resizing two neighbours keeps their sum and their minimums", () => {
  sizesClose(resizePair([0.5, 0.5], 0, 1, 0.1, [0, 0]), [0.6, 0.4]);
  sizesClose(resizePair([0.5, 0.5], 0, 1, 0.45, [0.1, 0.2]), [0.8, 0.2]);
  sizesClose(resizePair([0.5, 0.5], 0, 1, -0.45, [0.1, 0.2]), [0.1, 0.9]);
  sizesClose(resizePair([0.2, 0.3, 0.5], 1, 2, 0.1, [0, 0, 0]), [0.2, 0.4, 0.4]);
  sizesClose(resizePair([0.3, 0.3], 0, 1, 0.1, [0.4, 0.4]), [0.3, 0.3], "too small for both: shared by their minimums");
});

test("a split's sizes are set (normalised) and equalised", () => {
  let g = dockInGrid(DEFAULT_GRID, "a", { node: VP, edge: "right" });
  const id = (g as SplitNode).id;
  g = setSplitSizes(g, id, [3, 1]);
  sizesClose((g as SplitNode).sizes, [0.75, 0.25]);
  assert.equal(setSplitSizes(g, id, [3, 1]), g, "the same sizes: no change");
  assert.equal(setSplitSizes(g, id, [1, 1, 1]), g, "a count that does not match: no change");
  assert.equal(setSplitSizes(g, "nope", [1, 1]), g);
  sizesClose((equaliseSplit(g, id) as SplitNode).sizes, [0.5, 0.5]);
  assert.ok(findNode(g, id));
});

test("the grid as drawn: groups that show nothing take no room, their split's fractions are shared out", () => {
  let g = dockInGrid(DEFAULT_GRID, "a", { node: VP, edge: "right" });
  g = dockInGrid(g, "b", { node: VP, edge: "left" });
  g = dockInGrid(g, "c", { node: groupOf(g, "a")!.id, edge: "center" });
  const all = shownGrid(g, () => true);
  assert.equal(all.kind, "split");
  assert.deepEqual((all as { slots: number[] }).slots, [0, 1, 2]);
  const noB = shownGrid(g, (p) => p !== "b");
  assert.equal(shape(noB), "row(V [a,c])");
  assert.deepEqual((noB as { slots: number[] }).slots, [1, 2], "each shown child knows its stored slot");
  sizesClose((noB as SplitNode).sizes, [0.49 / 0.79, 0.3 / 0.79]);
  const onlyC = shownGrid(g, (p) => p === "c");
  assert.equal(shape(onlyC), "row(V [c])");
  assert.equal(groupOf(onlyC, "c")!.active, "c", "a hidden active tab gives way to a shown one");
  assert.deepEqual(shownGrid(g, () => false), DEFAULT_GRID, "nothing shown: the viewport alone");
});

test("the neighbour of a rect in a direction, by geometry", () => {
  const R = (left: number, top: number, width: number, height: number) => ({ left, top, width, height });
  const from = R(100, 100, 100, 100);
  const cands = [
    { id: "right-near", rect: R(200, 120, 50, 50) },
    { id: "right-far", rect: R(300, 100, 50, 100) },
    { id: "below", rect: R(100, 200, 100, 50) },
    { id: "above-off", rect: R(400, 0, 50, 50) },
  ];
  assert.equal(neighbourRect(from, "right", cands)?.id, "right-near");
  assert.equal(neighbourRect(from, "down", cands)?.id, "below");
  assert.equal(neighbourRect(from, "up", cands), null, "nothing above that overlaps across");
  assert.equal(neighbourRect(from, "left", cands), null);
});

test("a stored grid is read back as it was", () => {
  let g = dockInGrid(DEFAULT_GRID, "a", { node: VP, edge: "right" });
  g = dockInGrid(g, "b", { node: groupOf(g, "a")!.id, edge: "bottom" });
  g = dockInGrid(g, "c", { node: groupOf(g, "b")!.id, edge: "center" });
  g = setActiveTab(g, "b");
  assert.deepEqual(normaliseGrid(JSON.parse(JSON.stringify(g))), g);
});

test("a malformed stored grid is mended, or is the default", () => {
  assert.deepEqual(normaliseGrid(undefined), DEFAULT_GRID);
  assert.deepEqual(normaliseGrid({ kind: "group", id: "g1", panels: ["a"] }), DEFAULT_GRID, "no viewport");
  assert.deepEqual(
    normaliseGrid({ kind: "split", id: "s1", dir: "row", children: [{ kind: "viewport" }, { kind: "viewport" }], sizes: [1, 1] }),
    DEFAULT_GRID,
    "two viewports",
  );
  const mended = normaliseGrid({
    kind: "split",
    id: "s1",
    dir: "row",
    sizes: [2, "x", 1],
    children: [
      { kind: "viewport" },
      { kind: "group", id: "g1", panels: ["a", "a", 3, "b"], active: "zz" },
      { kind: "group", id: "g1", panels: ["b", "c"] },
      { kind: "group", id: "g9", panels: [] },
      { kind: "split", id: "s2", dir: "row", children: [{ kind: "nope" }] },
    ],
  });
  assert.equal(shape(mended), "row(V [a,b] [c])", "duplicates, empties and junk dropped");
  const groups = gridGroups(mended);
  assert.equal(groups[0].active, "a");
  assert.notEqual(groups[0].id, groups[1].id, "a repeated id is replaced");
  sumsToOne(mended);
  const nested = normaliseGrid({
    kind: "split",
    id: "s1",
    dir: "col",
    sizes: [1],
    children: [{ kind: "split", id: "s2", dir: "row", sizes: [1, 1], children: [{ kind: "viewport" }, { kind: "group", id: "g1", panels: ["a"] }] }],
  });
  assert.equal(shape(nested), "row(V [a])", "a split of one child is its child");
});
