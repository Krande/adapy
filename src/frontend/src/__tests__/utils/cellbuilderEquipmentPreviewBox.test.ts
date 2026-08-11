import assert from "node:assert/strict";
import { test } from "node:test";

import {
  aabbCenterSize,
  aabbCorners,
  equipmentDisplayBox,
  type AabbLike,
} from "../../utils/cellbuilder/equipmentPreviewBox";

test("equipmentDisplayBox fits the CAD's real (non-cubic) AABB, ignoring stored dims", () => {
  // A CAD mesh with DISTINCT extents (2 × 5 × 3), offset from the origin.
  const cad: AabbLike = { min: [1, 2, 3], max: [3, 7, 6] };
  // Stored dims are the useless 1×1×1 default — must be overridden entirely.
  const box = equipmentDisplayBox(cad, { lx: 1, ly: 1, lz: 1 });
  assert.deepEqual(box.min, [1, 2, 3]);
  assert.deepEqual(box.max, [3, 7, 6]);
  const { center, size } = aabbCenterSize(box);
  assert.deepEqual(size, [2, 5, 3]); // wraps the CAD's true extents
  assert.deepEqual(center, [2, 4.5, 4.5]);
});

test("equipmentDisplayBox falls back to the nominal lx/ly/lz box when no CAD", () => {
  // Z-up equipment convention: base at z=0, centred in x/y, lz = height.
  const box = equipmentDisplayBox(null, { lx: 2, ly: 4, lz: 6 });
  assert.deepEqual(box.min, [-1, 0, -2]);
  assert.deepEqual(box.max, [1, 6, 2]);
  const { size } = aabbCenterSize(box);
  assert.deepEqual(size, [2, 6, 4]);
});

test("aabbCorners returns the eight corners of the box", () => {
  const corners = aabbCorners({ min: [0, 0, 0], max: [1, 2, 3] });
  assert.equal(corners.length, 8);
  // every corner uses only min/max components on each axis
  for (const [x, y, z] of corners) {
    assert.ok(x === 0 || x === 1);
    assert.ok(y === 0 || y === 2);
    assert.ok(z === 0 || z === 3);
  }
  // the extreme corners are present
  assert.ok(corners.some(([x, y, z]) => x === 0 && y === 0 && z === 0));
  assert.ok(corners.some(([x, y, z]) => x === 1 && y === 2 && z === 3));
});

test("aabbCenterSize clamps a degenerate (inverted) box size to >= 0", () => {
  const { size } = aabbCenterSize({ min: [5, 0, 0], max: [1, 0, 0] });
  assert.deepEqual(size, [0, 0, 0]);
});
