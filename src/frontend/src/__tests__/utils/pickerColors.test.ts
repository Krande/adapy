/**
 * The picker's per-triangle colour map, built in its worker from runs `(startTri, triCount, id)`:
 * each triangle carries its run's id as r/g/b bytes; later runs (faces) overwrite earlier ones
 * (their solid); triangles no run covers stay 0; a run past the end is clipped.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { fillTriColors } from "../../utils/mesh_select/pickerColors";

const idAt = (c: Uint8Array, t: number) => c[t * 3] | (c[t * 3 + 1] << 8) | (c[t * 3 + 2] << 16);

test("each triangle carries its run's id, as three bytes", () => {
  const c = fillTriColors(4, Uint32Array.from([0, 2, 0x010203, 2, 2, 7]));
  assert.deepEqual([0, 1, 2, 3].map((t) => idAt(c, t)), [0x010203, 0x010203, 7, 7]);
  assert.deepEqual([...c.slice(0, 3)], [0x03, 0x02, 0x01], "r is the low byte");
});

test("a later run overwrites an earlier one -- a face over its solid", () => {
  const c = fillTriColors(5, Uint32Array.from([0, 5, 1, 1, 2, 9]));
  assert.deepEqual([0, 1, 2, 3, 4].map((t) => idAt(c, t)), [1, 9, 9, 1, 1]);
});

test("uncovered triangles stay 0 and a run past the end is clipped", () => {
  const c = fillTriColors(3, Uint32Array.from([2, 10, 5]));
  assert.equal(c.length, 9);
  assert.deepEqual([0, 1, 2].map((t) => idAt(c, t)), [0, 0, 5]);
});
