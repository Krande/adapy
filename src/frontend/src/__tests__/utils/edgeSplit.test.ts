import assert from "node:assert/strict";
import { test } from "node:test";

import { withoutEdges } from "../../utils/scene/fea/edgeSplit";

test("removes exactly the named pairs, keeping order of the rest", () => {
  const all = new Uint32Array([0, 1, 1, 2, 2, 3, 3, 4]);
  const remove = new Uint32Array([1, 2, 3, 4]);
  assert.deepEqual(Array.from(withoutEdges(all, remove)), [0, 1, 2, 3]);
});

test("matches a pair regardless of orientation on either side", () => {
  // The bake sorts pairs before deduping, but the split must not depend on it.
  const all = new Uint32Array([5, 2, 0, 1]);
  const remove = new Uint32Array([2, 5]);
  assert.deepEqual(Array.from(withoutEdges(all, remove)), [0, 1]);
});

test("returns the input array untouched when nothing is removed", () => {
  const all = new Uint32Array([0, 1, 1, 2]);
  assert.equal(withoutEdges(all, new Uint32Array()), all);
  // A remove list that names no present edge also keeps the original values.
  const kept = withoutEdges(all, new Uint32Array([7, 8]));
  assert.deepEqual(Array.from(kept), [0, 1, 1, 2]);
});

test("removing every edge yields an empty list", () => {
  const all = new Uint32Array([0, 1, 1, 2]);
  const removed = withoutEdges(all, new Uint32Array([1, 0, 2, 1]));
  assert.equal(removed.length, 0);
});

test("does not confuse pairs that would collide under naive concatenation", () => {
  // (1,23) vs (12,3): string keys like "1"+"23" and "12"+"3" collide; the
  // packed numeric key must not.
  const all = new Uint32Array([1, 23, 12, 3]);
  const remove = new Uint32Array([1, 23]);
  assert.deepEqual(Array.from(withoutEdges(all, remove)), [12, 3]);
});
