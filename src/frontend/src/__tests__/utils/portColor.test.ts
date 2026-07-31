/** Port colours resolve from an explicit override when present, otherwise from
 * the port category — the same rule the preview arrow, the editor swatch/accent
 * bar and any viewer overlay share, so they always agree on a colour. */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  CATEGORY_COLOR_HEX,
  normalizeHex,
  portColorHex,
  hexToInt,
  portColorInt,
} from "../../utils/portColor";

test("normalizeHex accepts #rrggbb and #rgb (any case), rejects the rest", () => {
  assert.equal(normalizeHex("#38BDF8"), "#38bdf8");
  assert.equal(normalizeHex("#abc"), "#aabbcc");
  assert.equal(normalizeHex("  #ABC  "), "#aabbcc");
  assert.equal(normalizeHex("red"), null);
  assert.equal(normalizeHex("#12"), null);
  assert.equal(normalizeHex(""), null);
  assert.equal(normalizeHex(null), null);
  assert.equal(normalizeHex(undefined), null);
});

test("portColorHex falls back to the category colour when no valid override", () => {
  assert.equal(portColorHex({ category: "process" }), CATEGORY_COLOR_HEX.process);
  assert.equal(portColorHex({ category: "electrical", color: null }), CATEGORY_COLOR_HEX.electrical);
  assert.equal(portColorHex({ category: "signal", color: "not-a-color" }), CATEGORY_COLOR_HEX.signal);
});

test("portColorHex uses a valid override regardless of category", () => {
  assert.equal(portColorHex({ category: "process", color: "#FF0000" }), "#ff0000");
});

test("hexToInt / portColorInt produce THREE 0xRRGGBB integers", () => {
  assert.equal(hexToInt("#38bdf8"), 0x38bdf8);
  assert.equal(portColorInt({ category: "process" }), hexToInt(CATEGORY_COLOR_HEX.process));
  assert.equal(portColorInt({ category: "process", color: "#010203" }), 0x010203);
});
