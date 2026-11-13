// The resized cell's dimension lines: the length labels, and which edges they go on.

import assert from "node:assert/strict";
import { test } from "node:test";

const { dimensionLines, formatLength } = await import("@/components/viewer/cellbuilder/cellDimensions");

test("lengths in metres, at most three decimals, no trailing zeros", () => {
  assert.equal(formatLength(6.875), "6.875m");
  assert.equal(formatLength(3.2), "3.2m");
  assert.equal(formatLength(4.35), "4.35m");
  assert.equal(formatLength(4), "4m");
  assert.equal(formatLength(1.23456), "1.235m");
  assert.equal(formatLength(0.1 + 0.2), "0.3m", "float dust is not shown");
  assert.equal(formatLength(-0.0001), "0m");
});

test("one line per axis, on the edge facing the eye, just outside the box", () => {
  const cell = { origin: [0, 0, 0] as [number, number, number], size: [6, 4, 3] as [number, number, number] };
  // Eye at +X, -Y, +Z of the box.
  const lines = dimensionLines(cell, [20, -10, 15], 0.2);
  assert.deepEqual(lines.map((l) => l.axis), [0, 1, 2]);
  const [x, y, z] = lines;
  assert.deepEqual([x.from, x.to, x.length], [[0, -0.2, 3.2], [6, -0.2, 3.2], 6], "X: the near (-Y) top (+Z) edge");
  assert.deepEqual([y.from, y.to, y.length], [[6.2, 0, 3.2], [6.2, 4, 3.2], 4], "Y: the +X top edge");
  assert.deepEqual([z.from, z.to, z.length], [[6.2, -0.2, 0], [6.2, -0.2, 3], 3], "Z: the +X -Y corner");
  // The camera goes round to -X: the Y and Z lines move to that side.
  const moved = dimensionLines(cell, [-20, -10, 15], 0.2);
  assert.equal(moved[1].from[0], -0.2);
  assert.equal(moved[2].from[0], -0.2);
  assert.notDeepEqual(moved.map((l) => l.sides), lines.map((l) => l.sides));
});
