/** Port colours resolve from an explicit override when present, otherwise from
 * a distinct per-port colour derived from the port name (with the category as a
 * hue anchor). The preview arrow, the editor swatch/accent bar and any viewer
 * overlay share this rule so they always agree on a colour, and two ports on the
 * same equipment never collide. */
import { test } from "node:test";
import assert from "node:assert/strict";
import {
  CATEGORY_COLOR_HEX,
  normalizeHex,
  portColorHex,
  hexToInt,
  portColorInt,
  uniquePortColorHex,
  uniquePortColorHexByIndex,
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

test("portColorHex without an override is a valid, deterministic per-port colour", () => {
  const a = portColorHex({ name: "suction", category: "process" });
  const b = portColorHex({ name: "suction", category: "process", color: null });
  assert.match(a, /^#[0-9a-f]{6}$/);
  assert.equal(a, b); // deterministic for the same name/category
  assert.equal(a, uniquePortColorHex("suction", "process"));
});

test("portColorHex gives distinct colours to distinct ports of the same category", () => {
  const suction = portColorHex({ name: "suction", category: "process" });
  const discharge = portColorHex({ name: "discharge", category: "process" });
  assert.notEqual(suction, discharge);
});

test("portColorHex by index gives every I/O a unique colour (golden-angle)", () => {
  // The pump ports (suction/discharge/power/signal) previously collided across
  // the name hash; index-based colouring guarantees distinct hues.
  const n = 6;
  const colours = Array.from({ length: n }, (_, i) =>
    portColorHex({ name: `p${i}`, category: "process" }, i),
  );
  assert.equal(new Set(colours).size, n); // all distinct
  colours.forEach((c) => assert.match(c, /^#[0-9a-f]{6}$/));
  // Index takes precedence over the name hash but an explicit override still wins.
  assert.equal(portColorHex({ name: "x", category: "process" }, 2), uniquePortColorHexByIndex(2));
  assert.equal(portColorHex({ name: "x", category: "process", color: "#abcdef" }, 2), "#abcdef");
});

test("portColorHex falls back to the category colour only when no name is given", () => {
  assert.equal(portColorHex({ category: "process" } as any), CATEGORY_COLOR_HEX.process);
  assert.equal(portColorHex({ name: "", category: "electrical" }), CATEGORY_COLOR_HEX.electrical);
});

test("portColorHex uses a valid override regardless of name/category", () => {
  assert.equal(portColorHex({ name: "power", category: "process", color: "#FF0000" }), "#ff0000");
  assert.equal(portColorHex({ name: "power", category: "signal", color: "not-a-color" }), uniquePortColorHex("power", "signal"));
});

test("hexToInt / portColorInt produce THREE 0xRRGGBB integers", () => {
  assert.equal(hexToInt("#38bdf8"), 0x38bdf8);
  assert.equal(portColorInt({ name: "in", category: "process", color: "#010203" }), 0x010203);
  assert.equal(portColorInt({ name: "in", category: "process" }), hexToInt(uniquePortColorHex("in", "process")));
});
