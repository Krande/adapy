/** The viewer's weld symbols draw what `ada.api.weld_symbols` draws: the same SVG, character for
 * character, from the generated geometry (scripts/codegen/gen_weld_symbols.py writes both that and the
 * fixture of Python's output this compares against). */
import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

import { weldSymbol, weldSymbolSvg, type WeldSymbolSvgOptions } from "@/utils/weldSymbols";
import { WELD_SYMBOLS } from "@/utils/weldSymbols.generated";

interface Expected {
  default: Record<string, string>;
  variants: { options: Record<string, unknown>; svgs: Record<string, string> }[];
}

const expected: Expected = JSON.parse(
  readFileSync(
    path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "fixtures", "weld_symbols", "expected_svgs.json"),
    "utf-8",
  ),
);

/** Python's keyword names as the TS options. */
function tsOptions(py: Record<string, unknown>): WeldSymbolSvgOptions {
  return {
    size: py.size as number | undefined,
    stroke: py.stroke as string | undefined,
    strokeWidth: py.stroke_width as number | undefined,
    referenceLine: py.reference_line as boolean | undefined,
    side: py.side as WeldSymbolSvgOptions["side"],
  };
}

test("every weld type's SVG is the one Python draws", () => {
  assert.deepEqual(Object.keys(WELD_SYMBOLS).sort(), Object.keys(expected.default).sort());
  assert.equal(Object.keys(expected.default).length, 26, "every WeldType but NONE");
  for (const [type, svg] of Object.entries(expected.default)) assert.equal(weldSymbolSvg(type), svg, type);
  for (const { options, svgs } of expected.variants) {
    for (const [type, svg] of Object.entries(svgs)) assert.equal(weldSymbolSvg(type, tsOptions(options)), svg, `${type} ${JSON.stringify(options)}`);
  }
});

test("a weld type is found by its value in any case, with or without WELD_TYPE_", () => {
  const fillet = weldSymbolSvg("FILLET");
  assert.ok(fillet);
  assert.equal(weldSymbolSvg("WELD_TYPE_FILLET"), fillet);
  assert.equal(weldSymbolSvg("weld_type_fillet"), fillet);
  assert.equal(weldSymbolSvg("Fillet"), fillet);
  assert.equal(weldSymbol("V"), weldSymbol("BEVEL_GROOVE_SINGLE_V_BUTT"), "the legacy alias, as WeldType.from_str");
});

test("no weld, an unknown type, or none given: no symbol", () => {
  for (const type of ["NONE", "WELD_TYPE_NONE", "WELD_TYPE_SOMETHING_NEW", "", null, undefined, "toString", "__proto__"]) {
    assert.equal(weldSymbolSvg(type), null, String(type));
    assert.equal(weldSymbol(type), null, String(type));
  }
});

test("the caller's size and colour", () => {
  const svg = weldSymbolSvg("SPOT", { size: 18, stroke: "#123456" })!;
  assert.match(svg, /^<svg xmlns="http:\/\/www.w3.org\/2000\/svg" width="18" height="18" viewBox="/);
  assert.match(svg, /<title>Spot weld<\/title>/);
  assert.doesNotMatch(svg, /currentColor/);
  assert.match(weldSymbolSvg("MELT_THROUGH")!, /fill="currentColor"/, "a fill takes the stroke colour");
});

test("the side of the line, ISO 2553 system A: the arrow side above, the other below, or both", () => {
  const ys = (svg: string) => [...svg.matchAll(/points="([^"]+)"/g)].flatMap((m) => m[1].split(" ").map((xy) => Number(xy.split(",")[1])));
  // SVG's y runs down: above the line is y < 0.
  const arrow = ys(weldSymbolSvg("FILLET")!);
  assert.ok(arrow.every((y) => y <= 0) && arrow.some((y) => y < 0));
  assert.equal(weldSymbolSvg("FILLET", { side: "arrow" }), weldSymbolSvg("FILLET"), "arrow is the default");
  const other = ys(weldSymbolSvg("FILLET", { side: "other" })!);
  assert.deepEqual(other, arrow.map((y) => (y === 0 ? 0 : -y)));
  const both = weldSymbolSvg("WELD_TYPE_FILLET", { side: "both" })!;
  assert.equal([...both.matchAll(/<polygon /g)].length, 2, "a double fillet: one above, one below");
  // Spot and seam sit on the line: one drawing on any side.
  for (const side of ["other", "both"] as const) assert.equal(weldSymbolSvg("SPOT", { side }), weldSymbolSvg("SPOT"));
  assert.throws(() => weldSymbolSvg("FILLET", { side: "left" as never }), /side must be/);
});