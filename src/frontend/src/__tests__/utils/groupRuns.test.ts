/**
 * A batched mesh's geometry groups -- one draw call each -- merge every run of neighbouring ranges
 * that share a material, selected and hidden runs as well as the default one. Selecting a whole
 * site was one draw per selected part, every frame.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { coalescedGroups, type Group } from "../../utils/mesh_select/groupRuns";

const g = (start: number, count: number, materialIndex: number): Group => ({ start, count, materialIndex });

test("a run of selected neighbours is ONE group, not one per range", () => {
  // Ten ranges of 3 indices, 2..7 selected (material 1).
  const starts = Array.from({ length: 10 }, (_, i) => i * 3);
  const counts = starts.map(() => 3);
  const groups = coalescedGroups(starts, counts, (i) => (i >= 2 && i <= 7 ? 1 : 0), 30, 0);
  assert.deepEqual(groups, [g(0, 6, 0), g(6, 18, 1), g(24, 6, 0)]);
});

test("nothing differing from the gap material is one group over everything", () => {
  assert.deepEqual(coalescedGroups([0, 3, 9], [3, 3, 3], () => 0, 15, 0), [g(0, 15, 0)]);
});

test("gaps take the gap material and break a run of another", () => {
  // Ranges [0,3) and [6,9) both selected, with an unowned gap [3,6) between and a tail to 12.
  assert.deepEqual(coalescedGroups([0, 6], [3, 3], () => 1, 12, 0), [g(0, 3, 1), g(3, 3, 0), g(6, 3, 1), g(9, 3, 0)]);
  // With the gap in the same material (the selection overlay: gaps invisible, others invisible).
  assert.deepEqual(coalescedGroups([0, 6], [3, 3], () => 1, 12, 1), [g(0, 12, 1)]);
});

test("alternating materials stay apart, and an empty mesh has no groups", () => {
  assert.deepEqual(coalescedGroups([0, 3, 6], [3, 3, 3], (i) => i % 2, 9, 0), [g(0, 3, 0), g(3, 3, 1), g(6, 3, 0)]);
  assert.deepEqual(coalescedGroups([], [], () => 0, 0, 0), []);
});
