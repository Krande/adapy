import assert from "node:assert/strict";
import { test } from "node:test";

import {
  MIN_MODEL_GAP,
  MODEL_GAP_FRACTION,
  cellsWidthX,
  nextModelOffsetX,
} from "@/utils/cellbuilder/modelPlacement";

// "Place next to existing" walks loaded things along +X so several can be
// compared. It applies to ANY loaded scene source — an uploaded GLB, a
// converted file, a procedural model's compiled result — because they are all
// groups in one map; nothing here knows what produced the geometry.

test("the first thing placed stays at the origin", () => {
  // Everything is authored around the origin, so a lone model must not appear
  // mysteriously off-centre.
  assert.equal(nextModelOffsetX([], 10), 0);
});

test("the second is placed past the first, with a proportional gap", () => {
  const offset = nextModelOffsetX([{ offsetX: 0, width: 10 }], 10);
  assert.equal(offset, 10 + 10 * MODEL_GAP_FRACTION);
  assert.ok(offset >= 10, "must clear the first model's far edge");
});

test("it clears the FAR edge of everything already placed", () => {
  // Not just the last one: a wide model placed earlier still has to be cleared.
  const placed = [
    { offsetX: 0, width: 10 },
    { offsetX: 15, width: 40 },
  ];
  const offset = nextModelOffsetX(placed, 10);
  assert.ok(offset >= 55, `expected past 55, got ${offset}`);
});

test("a zero-width model still clears its neighbour", () => {
  // A freshly created model has no cells; a purely proportional gap would be
  // zero and place it exactly on the edge, reading as part of the neighbour.
  const offset = nextModelOffsetX([{ offsetX: 0, width: 10 }], 0);
  assert.equal(offset, 10 + MIN_MODEL_GAP);
});

test("non-finite widths never yield NaN", () => {
  for (const bad of [NaN, Infinity, -Infinity]) {
    const offset = nextModelOffsetX([{ offsetX: 0, width: bad }], bad);
    assert.ok(Number.isFinite(offset), `width ${bad} yielded ${offset}`);
  }
});

test("placing three walks them along one axis in order", () => {
  const placed: { offsetX: number; width: number }[] = [];
  const widths = [10, 10, 10];
  const offsets = widths.map((w) => {
    const x = nextModelOffsetX(placed, w);
    placed.push({ offsetX: x, width: w });
    return x;
  });
  assert.deepEqual(offsets, [0, 15, 30]);
  // Strictly increasing, so the reading order matches the placing order.
  assert.ok(offsets[0] < offsets[1] && offsets[1] < offsets[2]);
});

test("cellsWidthX measures the span, not the count", () => {
  assert.equal(cellsWidthX([{ origin: [0, 0, 0], size: [5, 1, 1] }]), 5);
  assert.equal(
    cellsWidthX([
      { origin: [0, 0, 0], size: [5, 1, 1] },
      { origin: [5, 0, 0], size: [5, 1, 1] },
    ]),
    10,
  );
  // A cell that does not start at zero contributes its true far edge.
  assert.equal(cellsWidthX([{ origin: [20, 0, 0], size: [5, 1, 1] }]), 0 || 5);
});

test("an empty model measures zero rather than Infinity", () => {
  assert.equal(cellsWidthX([]), 0);
});
