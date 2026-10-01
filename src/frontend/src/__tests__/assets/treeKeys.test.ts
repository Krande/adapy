/**
 * The Sources tree's keys and ranges, over a flat list of visible rows:
 *
 *   site-a          (open)
 *     zone-1        (closed, has children)
 *     zone-2        (leaf)
 *   site-b          (closed, has children)
 *   site-c          (leaf)
 */

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import { actionTargets, rangeIds, treeKeyAction, type KeyRow } from "../../assets/treeKeys";
import { useAssetBrowserStore } from "../../state/assetBrowserStore";

const ROWS: KeyRow[] = [
  { id: "site-a", depth: 0, hasChildren: true, expanded: true },
  { id: "zone-1", depth: 1, hasChildren: true, expanded: false },
  { id: "zone-2", depth: 1, hasChildren: false, expanded: false },
  { id: "site-b", depth: 0, hasChildren: true, expanded: false },
  { id: "site-c", depth: 0, hasChildren: false, expanded: false },
];

test("Up/Down move one row, clamped at the ends; Shift says extend", () => {
  assert.deepEqual(treeKeyAction(ROWS, "zone-1", "ArrowDown", false), { kind: "focus", id: "zone-2", extend: false });
  assert.deepEqual(treeKeyAction(ROWS, "zone-1", "ArrowUp", true), { kind: "focus", id: "site-a", extend: true });
  assert.equal(treeKeyAction(ROWS, "site-c", "ArrowDown", false), null, "nothing below the last row");
  assert.equal(treeKeyAction(ROWS, "site-a", "ArrowUp", false), null, "nothing above the first");
});

test("with nothing focused, the first move lands on the top row", () => {
  assert.deepEqual(treeKeyAction(ROWS, null, "ArrowDown", false), { kind: "focus", id: "site-a", extend: false });
  assert.deepEqual(treeKeyAction(ROWS, null, "ArrowUp", false), { kind: "focus", id: "site-a", extend: false });
});

test("Home/End jump to the ends", () => {
  assert.deepEqual(treeKeyAction(ROWS, "zone-1", "End", true), { kind: "focus", id: "site-c", extend: true });
  assert.deepEqual(treeKeyAction(ROWS, "zone-1", "Home", false), { kind: "focus", id: "site-a", extend: false });
});

test("Right opens a closed branch, then steps into it; does nothing on a leaf", () => {
  assert.deepEqual(treeKeyAction(ROWS, "site-b", "ArrowRight", false), { kind: "expand", id: "site-b" });
  assert.deepEqual(treeKeyAction(ROWS, "site-a", "ArrowRight", false), { kind: "focus", id: "zone-1", extend: false });
  assert.equal(treeKeyAction(ROWS, "zone-2", "ArrowRight", false), null);
});

test("Right on an open branch whose level is still loading has nothing to step into", () => {
  const loading: KeyRow[] = [{ id: "site-a", depth: 0, hasChildren: true, expanded: true }, ROWS[3]];
  assert.equal(treeKeyAction(loading, "site-a", "ArrowRight", false), null);
});

test("Left closes an open branch; on a closed one or a leaf it goes to the level above", () => {
  assert.deepEqual(treeKeyAction(ROWS, "site-a", "ArrowLeft", false), { kind: "collapse", id: "site-a" });
  assert.deepEqual(treeKeyAction(ROWS, "zone-1", "ArrowLeft", false), { kind: "focus", id: "site-a", extend: false });
  assert.deepEqual(treeKeyAction(ROWS, "zone-2", "ArrowLeft", true), { kind: "focus", id: "site-a", extend: false });
  assert.equal(treeKeyAction(ROWS, "site-b", "ArrowLeft", false), null, "a closed top-level row has no level above");
});

test("other keys are not the tree's", () => {
  assert.equal(treeKeyAction(ROWS, "site-a", "a", false), null);
  assert.equal(treeKeyAction([], null, "ArrowDown", false), null);
});

test("a range runs from the anchor to the focus in tree order, either direction", () => {
  assert.deepEqual(rangeIds(ROWS, "zone-1", "site-b"), ["zone-1", "zone-2", "site-b"]);
  assert.deepEqual(rangeIds(ROWS, "site-c", "zone-2"), ["zone-2", "site-b", "site-c"]);
  assert.deepEqual(rangeIds(ROWS, "site-a", "site-a"), ["site-a"]);
});

test("a range without a visible anchor is just the focus", () => {
  assert.deepEqual(rangeIds(ROWS, null, "site-b"), ["site-b"]);
  assert.deepEqual(rangeIds(ROWS, "collapsed-away", "site-b"), ["site-b"]);
});

const PARENT: Record<string, string | null> = { "site-a": null, "zone-1": "site-a", "zone-2": "site-a", "site-b": null, "site-c": null };
const parentOf = (id: string) => PARENT[id];

test("an action on a row outside the selection, or on a selection of one, is about that row", () => {
  assert.deepEqual(actionTargets(new Set(["site-a", "site-b"]), "site-c", parentOf), ["site-c"]);
  assert.deepEqual(actionTargets(new Set(["site-b"]), "site-b", parentOf), ["site-b"]);
});

test("an action on a row in the selection applies to its topmost rows, in selection order", () => {
  // zone-1 sits under the selected site-a: site-a's subtree already carries it.
  assert.deepEqual(actionTargets(new Set(["site-b", "site-a", "zone-1"]), "zone-1", parentOf), ["site-b", "site-a"]);
  assert.deepEqual(actionTargets(new Set(["zone-1", "zone-2"]), "zone-2", parentOf), ["zone-1", "zone-2"], "siblings both stay");
});

beforeEach(() => useAssetBrowserStore.getState().resetForScope("user:me"));

test("the store: a refresh keeps what is open and selected; a collection switch does not", () => {
  const s = () => useAssetBrowserStore.getState();
  s().setExpanded("site-a", true);
  s().select("zone-1");
  s().selectRange(["zone-1", "zone-2"], "zone-2");
  s().resetForest();
  assert.ok(s().expanded.has("site-a"));
  assert.deepEqual([...s().selection], ["zone-1", "zone-2"]);
  assert.equal(s().selected, "zone-2");
  assert.equal(s().anchor, "zone-1");
  s().setCollection("another");
  assert.equal(s().expanded.size, 0);
  assert.equal(s().selection.size, 0);
});

test("the store: a click selects one and anchors there; a range keeps the anchor", () => {
  const s = () => useAssetBrowserStore.getState();
  s().select("zone-1");
  assert.deepEqual([...s().selection], ["zone-1"]);
  assert.equal(s().anchor, "zone-1");
  s().selectRange(rangeIds(ROWS, s().anchor, "site-b"), "site-b");
  assert.deepEqual([...s().selection], ["zone-1", "zone-2", "site-b"]);
  assert.equal(s().selected, "site-b");
  assert.equal(s().anchor, "zone-1", "a second shift-click re-ranges from the same anchor");
  s().selectRange(rangeIds(ROWS, s().anchor, "site-a"), "site-a");
  assert.deepEqual([...s().selection], ["site-a", "zone-1"]);
});

test("the store: ctrl-click toggles one row and re-anchors; a scope switch clears it all", () => {
  const s = () => useAssetBrowserStore.getState();
  s().select("site-a");
  s().toggleSelected("site-c");
  assert.deepEqual([...s().selection].sort(), ["site-a", "site-c"]);
  assert.equal(s().anchor, "site-c");
  s().toggleSelected("site-a");
  assert.deepEqual([...s().selection], ["site-c"]);
  assert.equal(s().selected, "site-a", "focus stays on the row the click was about");
  s().resetForScope("user:me");
  assert.equal(s().selection.size, 0);
  assert.equal(s().anchor, null);
});
