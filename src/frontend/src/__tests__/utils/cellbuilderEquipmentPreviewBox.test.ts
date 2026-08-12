import assert from "node:assert/strict";
import { test } from "node:test";

import {
  bboxLooksUninferred,
  equipmentBoxCenterSize,
  equipmentBoxCorners,
} from "../../utils/cellbuilder/equipmentPreviewBox";

test("equipmentBoxCenterSize is Z-up: BoxGeometry(lx,ly,lz), base at z=0, centred on x/y", () => {
  const { center, size } = equipmentBoxCenterSize({ lx: 2, ly: 4, lz: 6 });
  // size is (lx, ly, lz) verbatim — lz is the vertical (height) extent.
  assert.deepEqual(size, [2, 4, 6]);
  // footprint centred on x/y, box base sitting on z=0 → centre at (0, 0, lz/2)
  assert.deepEqual(center, [0, 0, 3]);
});

test("equipmentBoxCenterSize clamps negative dims to >= 0", () => {
  const { size } = equipmentBoxCenterSize({ lx: -1, ly: 0, lz: 3 });
  assert.deepEqual(size, [0, 0, 3]);
});

test("equipmentBoxCorners are Z-up: footprint on x/y ∈ ±l/2, height on z ∈ [0, lz]", () => {
  const corners = equipmentBoxCorners({ lx: 2, ly: 4, lz: 6 });
  assert.equal(corners.length, 8);
  for (const [x, y, z] of corners) {
    assert.ok(x === -1 || x === 1); // ±lx/2
    assert.ok(y === -2 || y === 2); // ±ly/2
    assert.ok(z === 0 || z === 6); // base .. height
  }
  // the base + top extreme corners exist
  assert.ok(corners.some(([x, y, z]) => x === -1 && y === -2 && z === 0));
  assert.ok(corners.some(([x, y, z]) => x === 1 && y === 2 && z === 6));
});

test("bboxLooksUninferred flags the cubic default + non-positive dims, not real extents", () => {
  assert.ok(bboxLooksUninferred({ lx: 1, ly: 1, lz: 1 })); // 1×1×1 seed
  assert.ok(bboxLooksUninferred({ lx: 2, ly: 2, lz: 2 })); // any cube
  assert.ok(bboxLooksUninferred({ lx: 0, ly: 3, lz: 2 })); // non-positive
  assert.ok(!bboxLooksUninferred({ lx: 2, ly: 4, lz: 6 })); // real, inferred
  assert.ok(!bboxLooksUninferred({ lx: 1, ly: 1, lz: 2.5 })); // non-cubic
});
