// Panels open together in the classic panel column stack; they are never drawn over each other.
//
// The column is a flex column capped at the window less the top bar. With the Connections panel
// and the Selected Object Info card open together (a clash marker clicked opens both), each panel
// was a flex item of the column, and how it took being shrunk was its own root's business: the
// Connections frame did not clip on desktop, so it kept its height and the card beside it was
// squeezed to a sliver under it. Now every panel sits in a slot of the column's own
// (`ColumnSlot`): the slots share the column, each scrolls what does not fit in it, and an empty
// one (a closed panel) is not drawn.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { CLASSIC_COLUMN_SLOT_CLASS, COLUMN_SLOT_ATTR, ColumnSlot } from "@/layout/presets/columnSlot";
import { SIMULATIONS_FRAME_CLASS } from "@/components/simulations/SimulationsFrame";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const SRC = path.join(FRONTEND, "src");
const read = (rel: string) => readFileSync(path.join(SRC, rel), "utf8");
const SLOT = new Set(CLASSIC_COLUMN_SLOT_CLASS.split(/\s+/).filter(Boolean));

/** The column's children as drawn: each slot's panel id and what is in it. */
function slots(html: string): { id: string; inner: string }[] {
  const re = new RegExp(`<div class="([^"]*)" ${COLUMN_SLOT_ATTR}="([^"]*)">(.*?)</div><!--end-->`, "g");
  return [...html.matchAll(re)].map((m) => {
    assert.equal(m[1], CLASSIC_COLUMN_SLOT_CLASS, "every slot carries the slot class");
    return { id: m[2], inner: m[3] };
  });
}

/** A column of slots as the classic column draws them; `end` marks each slot's end for `slots`. */
function column(panels: [string, React.ReactNode][]): string {
  const html = renderToStaticMarkup(
    <div>
      {panels.map(([id, body]) => (
        <ColumnSlot key={id} panelId={id}>
          {body}
          <i data-end="" />
        </ColumnSlot>
      ))}
    </div>,
  );
  return html.replace(/<i data-end=""><\/i><\/div>/g, "</div><!--end-->");
}

test("two panels open in the column are two slots, one after the other, each holding its own panel", () => {
  const html = column([
    ["core:object-info", <div className="max-h-[80svh] overflow-y-auto">Selected Object Info</div>],
    ["core:component-controls", <div className={SIMULATIONS_FRAME_CLASS}>Connections</div>],
  ]);
  const drawn = slots(html);
  assert.deepEqual(drawn.map((s) => s.id), ["core:object-info", "core:component-controls"], "stacked in placement order");
  assert.match(drawn[0].inner, /Selected Object Info/);
  assert.doesNotMatch(drawn[0].inner, /Connections/, "neither is drawn inside the other's slot");
  assert.match(drawn[1].inner, /Connections/);
  assert.doesNotMatch(drawn[1].inner, /Selected Object Info/);
});

test("a slot is in the column's flow, shares its height and keeps what its panel draws inside it", () => {
  // In the flow, one below the next: never placed (absolute or fixed) where another panel is.
  for (const c of SLOT) assert.doesNotMatch(c, /^(?:absolute|fixed|sticky|relative|inset|top-|left-|-?m[tb]-)/, `${c} would take it out of the stack`);
  // Shrinks with the other slots (in proportion to what each needs) when the column is full...
  assert.ok(SLOT.has("min-h-0") && SLOT.has("shrink"), "it shrinks below its content");
  assert.ok(!SLOT.has("shrink-0") && !SLOT.has("flex-none"), "no slot keeps its full height at the others' cost");
  // ...and scrolls what it cannot show: a panel's content never spills over the slot below.
  assert.ok(SLOT.has("overflow-y-auto"), "the slot clips and scrolls its panel");
  // A panel that scrolls itself (a pinned heading over a scrolling body) shrinks into the slot.
  assert.ok(SLOT.has("flex") && SLOT.has("flex-col"));
});

test("a closed panel gives its room back: its slot is empty and not drawn", () => {
  const html = renderToStaticMarkup(
    <div>
      <ColumnSlot panelId="core:object-info">{null}</ColumnSlot>
      <ColumnSlot panelId="core:component-controls">{false}</ColumnSlot>
    </div>,
  );
  // Nothing in it, not even whitespace: `:empty` holds, and `empty:hidden` takes it (and its gap) out.
  assert.equal(html, `<div><div class="${CLASSIC_COLUMN_SLOT_CLASS}" ${COLUMN_SLOT_ATTR}="core:object-info"></div><div class="${CLASSIC_COLUMN_SLOT_CLASS}" ${COLUMN_SLOT_ATTR}="core:component-controls"></div></div>`);
  assert.ok(SLOT.has("empty:hidden"));
});

test("the classic column puts every panel it draws in a slot: core's, and the plugins' region", () => {
  const classic = read("layout/presets/classic.tsx");
  // Core's panels at home in the column.
  assert.match(classic, /<ColumnSlot panelId=\{p\.id\}>\s*<ColumnPanel\b/);
  assert.equal((classic.match(/<ColumnPanel\b/g) ?? []).length, 1, "no column panel drawn outside a slot");
  // The plugins' `top-panel` region: each of its panels, framed or not.
  assert.match(classic, /<PluginPanelRegion[\s\S]*?region="top-panel"[\s\S]*?slot=\{\(slot, node\) => <ColumnSlot panelId=\{slot\.id\}>\{node\}<\/ColumnSlot>\}/);
  const region = read("plugins/PluginSlots.tsx");
  assert.match(region, /if \(slot\) return <React\.Fragment key=\{panel\.id\}>\{slot\(panel, drawn\)\}<\/React\.Fragment>/);
});

test("the slot's classes make the rules they say: shrink to nothing, scroll, hidden when empty", async () => {
  const require = createRequire(path.join(FRONTEND, "package.json"));
  const { compile } = require("@tailwindcss/node") as {
    compile: (css: string, opts: { base: string; onDependency: (p: string) => void }) => Promise<{ build: (c: string[]) => string }>;
  };
  const cssPath = path.join(SRC, "app.css");
  const compiler = await compile(readFileSync(cssPath, "utf8"), { base: path.dirname(cssPath), onDependency() {} });
  const css = compiler.build([...SLOT]).replace(/\s+/g, " ");
  const rule = (cls: string) => {
    // Its selector (a variant's `:` escaped), then its block (a variant's nested one too).
    const at = css.indexOf(`.${cls.replace(/:/g, "\\:")} `);
    assert.ok(at >= 0, `a rule for ${cls}`);
    return css.slice(at, css.indexOf("}", at) + 1);
  };
  assert.match(rule("min-h-0"), /min-height: (?:0|calc\(var\(--spacing\) \* 0\))/);
  assert.match(rule("overflow-y-auto"), /overflow-y: auto/);
  assert.match(rule("shrink"), /flex-shrink: 1/);
  const empty = rule("empty:hidden");
  assert.match(empty, /:empty/);
  assert.match(empty, /display: none/);
});
