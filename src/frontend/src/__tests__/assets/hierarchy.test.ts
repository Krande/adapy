// The forest: incremental assembly, absent parents, subtree folds, flattening.
// Ported verbatim (same API) from the out-of-tree plugin's hierarchy module.

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  ancestorsOf,
  buildHierarchy,
  flattenVisible,
  foldSubtrees,
  type HierarchyInput,
} from "../../assets/hierarchy";

const n = (id: string, parent: string | null, payload = 0): HierarchyInput<number> => ({
  id,
  parent,
  data: payload,
});

test("depth and parent-before-child order come out of one pass", () => {
  const h = buildHierarchy([n("a", null), n("b", "a"), n("c", "b"), n("d", "a")]);
  assert.deepEqual(h.roots, ["a"]);
  assert.equal(h.byId.get("c")?.depth, 2);
  assert.deepEqual(h.order, ["a", "b", "d", "c"]);
  for (const id of h.order) {
    const parent = h.byId.get(id)?.parent;
    if (parent) assert.ok(h.order.indexOf(parent) < h.order.indexOf(id), id);
  }
});

test("a node whose parent has not been fetched becomes a root, and says so", () => {
  // The normal case, not corruption: the tab holds a collection index and one
  // subtree spine, and a row rooted deeper arrives before its spine does.
  const h = buildHierarchy([n("orphan", "not-fetched-yet"), n("a", null)]);
  assert.deepEqual([...h.roots].sort(), ["a", "orphan"]);
  assert.equal(h.byId.get("orphan")?.parent, null);
  assert.equal(h.byId.get("orphan")?.declaredParent, "not-fetched-yet");
});

test("a later duplicate replaces an earlier one — the richer spine wins over the index stub", () => {
  const h = buildHierarchy([n("s", null, 0), n("s", null, 7)]);
  assert.equal(h.byId.get("s")?.data, 7);
  assert.equal(h.byId.size, 1);
});

test("a cycle terminates and its members surface as stray roots rather than vanishing", () => {
  const h = buildHierarchy([n("a", null), n("x", "y"), n("y", "x")]);
  assert.equal(h.byId.size, 3);
  assert.ok(h.roots.includes("x"));
  assert.ok(h.roots.includes("y"));
  assert.deepEqual(ancestorsOf(h, "x"), []);
});

test("ancestors come back nearest first", () => {
  const h = buildHierarchy([n("a", null), n("b", "a"), n("c", "b")]);
  assert.deepEqual(ancestorsOf(h, "c"), ["b", "a"]);
  assert.deepEqual(ancestorsOf(h, "a"), []);
});

test("foldSubtrees aggregates bottom-up in one reverse pass", () => {
  const h = buildHierarchy([n("a", null, 0), n("b", "a", 1), n("c", "b", 2), n("d", "a", 0)]);
  const totals = foldSubtrees(h, (node) => node.data);
  assert.equal(totals.get("a"), 3);
  assert.equal(totals.get("b"), 3);
  assert.equal(totals.get("c"), 2);
  assert.equal(totals.get("d"), 0);
});

test("foldSubtrees does not recurse — a deep chain must not overflow", () => {
  const deep: HierarchyInput<number>[] = [];
  for (let i = 0; i < 50_000; i++) deep.push(n(`n${i}`, i === 0 ? null : `n${i - 1}`, 1));
  const h = buildHierarchy(deep);
  assert.equal(h.byId.get("n49999")?.depth, 49999);
  assert.equal(foldSubtrees(h, (node) => node.data).get("n0"), 50_000);
  assert.equal(flattenVisible(h, new Set()).length, 1);
});

test("flattenVisible walks only what is expanded, in display order", () => {
  const h = buildHierarchy([n("a", null), n("b", "a"), n("c", "b"), n("d", "a")]);
  assert.deepEqual(flattenVisible(h, new Set()).map((r) => r.id), ["a"]);
  assert.deepEqual(flattenVisible(h, new Set(["a"])).map((r) => r.id), ["a", "b", "d"]);
  assert.deepEqual(flattenVisible(h, new Set(["a", "b"])).map((r) => r.id), ["a", "b", "c", "d"]);
});

test("a node with no fetched children is still expandable when the caller says so", () => {
  const h = buildHierarchy([n("area-1", null)]);
  assert.equal(flattenVisible(h, new Set())[0].hasChildren, false);
  const rows = flattenVisible(h, new Set(), { expandable: (id) => id === "area-1" });
  assert.equal(rows[0].hasChildren, true);
  // Expanded-but-nothing-fetched-yet must not claim to be open: the chevron
  // would point down over no rows.
  assert.equal(rows[0].expanded, false);
  const opened = flattenVisible(h, new Set(["area-1"]), { expandable: () => true });
  assert.equal(opened[0].expanded, true);
});

// --- `include`: the tab's kind filter --------------------------------- //

test("include drops the rows outside it and keeps display order", () => {
  const h = buildHierarchy([n("a", null), n("b", "a"), n("c", "b"), n("d", "a")]);
  // Ancestor-closed, as the contract requires: c is kept, so b and a are too.
  const include = new Set(["a", "b", "c"]);
  assert.deepEqual(flattenVisible(h, new Set(["a", "b"]), { include }).map((r) => r.id), ["a", "b", "c"]);
});

test("a root outside the include set does not appear", () => {
  const h = buildHierarchy([n("a", null), n("z", null), n("b", "a")]);
  assert.deepEqual(
    flattenVisible(h, new Set(["a"]), { include: new Set(["a", "b"]) }).map((r) => r.id),
    ["a", "b"],
  );
});

test("a row whose every child was filtered out loses its twisty", () => {
  // Otherwise the filter leaves an expandable row that opens onto nothing —
  // the user clicks and the tree does not move.
  const h = buildHierarchy([n("a", null), n("b", "a")]);
  const rows = flattenVisible(h, new Set(["a"]), { include: new Set(["a"]) });
  assert.deepEqual(rows.map((r) => r.id), ["a"]);
  assert.equal(rows[0].hasChildren, false);
  assert.equal(rows[0].expanded, false);
});

test("include does not suppress `expandable` — an unfetched spine stays openable", () => {
  // The filter must never hide a branch for the reason that nobody has looked
  // inside it yet. A branch kept by the unfetched clause still has to offer the
  // expansion that fetches its spine.
  const h = buildHierarchy([n("area-1", null)]);
  const rows = flattenVisible(h, new Set(), {
    include: new Set(["area-1"]),
    expandable: () => true,
  });
  assert.equal(rows[0].hasChildren, true);
});

test("depth survives filtering, so indentation still lines up", () => {
  const h = buildHierarchy([n("a", null), n("b", "a"), n("c", "b")]);
  const rows = flattenVisible(h, new Set(["a", "b"]), { include: new Set(["a", "b", "c"]) });
  assert.deepEqual(rows.map((r) => r.depth), [0, 1, 2]);
});

test("the kind filter keeps the branch whole — it does not flatten to the assets", () => {
  // How the tab builds `include`: fold "roots a published entry" upward. The
  // closure is what makes it legal to pass to flattenVisible without a second
  // pass, AND what keeps the containers above an asset on screen. A filter that
  // kept only the entries themselves would render memberA at depth 0 with no
  // area above it, which is not a tree.
  const h = buildHierarchy([
    n("area-1", null, 0),
    n("level-a", "area-1", 0),
    n("member-a", "level-a", 1), // has a published entry
    n("level-b", "area-1", 0), // nothing published anywhere under it
  ]);
  const totals = foldSubtrees(h, (node) => node.data);
  const include = new Set([...totals].filter(([, v]) => v > 0).map(([k]) => k));
  // level-b is gone; the chain that PLACES member-a is not.
  assert.deepEqual([...include].sort(), ["area-1", "level-a", "member-a"]);
  const rows = flattenVisible(h, new Set(["area-1", "level-a"]), { include });
  assert.deepEqual(rows.map((r) => r.id), ["area-1", "level-a", "member-a"]);
  assert.deepEqual(rows.map((r) => r.depth), [0, 1, 2]);
});

test("an area with nothing published under it drops out entirely", () => {
  const h = buildHierarchy([n("areaA", null, 1), n("areaB", null, 0), n("levelB", "areaB", 0)]);
  const totals = foldSubtrees(h, (node) => node.data);
  const include = new Set([...totals].filter(([, v]) => v > 0).map(([k]) => k));
  assert.deepEqual(flattenVisible(h, new Set(["areaA", "areaB"]), { include }).map((r) => r.id), ["areaA"]);
});
