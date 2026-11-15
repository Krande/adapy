// A divider beside panels held at a px size resizes the one whose edge facing the 3D
// view it is: right of the view, the panel after it; left of the view, the one before.

import assert from "node:assert/strict";
import { test } from "node:test";

const V = await import("@/layout/presets/viewSide");

const g = (id: string) => ({ kind: "group", id });
const view = { kind: "viewport" };
const split = (...children: { kind: string }[]) => ({ kind: "split", children });

test("in a row with the view, a divider belongs to the panel on its far side from the view", () => {
  // storage | run | VIEW | overview | input | results
  const row = [g("storage"), g("run"), view, g("overview"), g("input"), g("results")];
  const owner = (i: number) => (V.preferAfter(V.viewSideOfDivider(row, i)) ? i + 1 : i);
  assert.equal(owner(0), 0, "storage|run: storage's right edge");
  assert.equal(owner(1), 1, "run|view: run's right edge");
  assert.equal(owner(2), 3, "view|overview: overview's left edge");
  assert.equal(owner(3), 4, "overview|input: input's left edge");
  assert.equal(owner(4), 5, "input|results: results' left edge, the only one it has");
});

test("a dock nested in its own split inherits which side the view is on", () => {
  const root = [g("left"), view, split(g("a"), g("b"))];
  const sides = V.childViewSides(root, "row", 2, {});
  assert.equal(sides.row, "before", "the view is before the right dock");
  assert.equal(V.viewSideOfDivider([g("a"), g("b")], 0, sides.row), "before");
  assert.equal(V.childViewSides(root, "row", 0, {}).row, "after");
  // The child holding the view decides for itself; the cross direction passes through.
  assert.equal(V.childViewSides([split(view, g("x")), g("y")], "row", 0, { col: "after" }).row, undefined);
  assert.equal(V.childViewSides([split(view, g("x")), g("y")], "row", 0, { col: "after" }).col, "after");
  assert.equal(V.preferAfter(undefined), false, "no view either way: as before, the panel before it");
});
