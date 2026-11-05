import assert from "node:assert/strict";
import { test } from "node:test";

import {
  SHEET_SNAPS,
  clampSheetPx,
  settleSheet,
  sheetMaxPx,
} from "@/utils/useBottomSheet";

// The bottom sheet's arithmetic, shared by every panel that is a sheet on a phone
// (the cellbuilder panel carried a copy of it until it adopted the shared hook).

test("the sheet never reaches higher than 56 px from the top, nor below 120 px tall at most", () => {
  assert.equal(sheetMaxPx(844), 788);
  assert.equal(sheetMaxPx(100), 120);
});

test("a dragged height is clamped between 80 px and the cap", () => {
  assert.equal(clampSheetPx(10, 700), 80);
  assert.equal(clampSheetPx(900, 700), 700);
  assert.equal(clampSheetPx(300, 700), 300);
});

test("released small, the sheet is dismissed", () => {
  // below 18 % of an 844 px viewport
  assert.equal(settleSheet(150, 844, sheetMaxPx(844)), null);
});

test("released anywhere else, it snaps to the nearest of peek, half and full", () => {
  const vh = 844;
  const max = sheetMaxPx(vh);
  const [peek, half, full] = SHEET_SNAPS.map((f) => clampSheetPx(vh * f, max));
  assert.equal(settleSheet(peek + 20, vh, max), peek);
  assert.equal(settleSheet(half - 30, vh, max), half);
  assert.equal(settleSheet(max, vh, max), full);
});

test("snap points never exceed the cap of a short visible viewport", () => {
  // the visible viewport (toolbar shown) is shorter than the layout viewport
  const settled = settleSheet(700, 844, 500);
  assert.equal(settled, 500);
});
