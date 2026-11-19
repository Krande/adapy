// The top bar stays on screen, whatever the panels under it hold.
//
// The top bar and the panel column are one absolutely placed box over the viewport, in a page that
// does not scroll (`html, body { overflow: hidden }`). Two capped panels stacked (Selected Object
// Info and Connections: a clash marker clicked opens both) made the box taller than the window, and
// a joint row brought into view with `scrollIntoView` -- which scrolls every ancestor, an
// overflow-hidden one too -- scrolled the whole box up, the top bar with it. Now the box is capped
// at the window, the column scrolls inside what the top bar leaves, and rows are brought into view
// in their own scroll box only (`revealInScroller`).
//
// And the classes that do it are real: each one, and every class of the menu-column frames, makes
// a rule in the Tailwind build the viewer is built with (app.css, its @config), and the build's
// source scan sees the files they are written in -- a class Tailwind does not generate is silently
// nothing, which is how a "max-h" that does not exist lets a panel grow off the screen.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { scrollTopFor } from "@/utils/dom/revealInScroller";

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const SRC = path.join(FRONTEND, "src");
const read = (rel: string) => readFileSync(path.join(SRC, rel), "utf8");

/** The string a `const NAME =` in `source` is, its literal parts joined (a `"..." + "..."` chain). */
function constString(source: string, name: string): string {
  const m = new RegExp(`export const ${name}\\s*=([\\s\\S]*?);\\r?\\n`).exec(source);
  assert.ok(m, `${name} is defined`);
  return [...m[1].matchAll(/"([^"]*)"/g)].map((x) => x[1]).join("");
}

const classic = read("layout/presets/classic.tsx");
const MENU = constString(classic, "CLASSIC_MENU_CLASS").split(/\s+/).filter(Boolean);
const COLUMN = constString(classic, "CLASSIC_COLUMN_CLASS").split(/\s+/).filter(Boolean);

test("the box over the viewport is never taller than the window, and its column scrolls", () => {
  assert.ok(MENU.includes("max-h-[100dvh]") && MENU.includes("flex-col"), MENU.join(" "));
  for (const c of ["min-h-0", "overflow-y-auto", "flex-col"]) assert.ok(COLUMN.includes(c), `column has ${c}`);
  assert.match(classic, /className=\{CLASSIC_MENU_CLASS\}/);
  assert.match(classic, /className=\{CLASSIC_COLUMN_CLASS\}/);
  // The top bar keeps its height: the column is what gives way.
  assert.match(classic, /"flex flex-row items-center gap-2 px-2 max-w-full min-w-0 shrink-0 pointer-events-auto/);
});

test("the menu-column panels bring a row into view in their own scroll box, not the page", () => {
  for (const rel of [
    "components/info_box_scene/joints/IdentifiedJoints.tsx",
    "components/viewer/cellbuilder/panel/SystemsTab.tsx",
    "components/storage/browser/useListKeyboardNav.ts",
    "layout/optionCommands.ts",
  ]) {
    const src = read(rel);
    assert.doesNotMatch(src, /\.scrollIntoView\(|\.scrollIntoView\?\.\(/, `${rel} calls no scrollIntoView`);
    assert.match(src, /revealInScroller\(/, `${rel} reveals in its scroll box`);
  }
});

test("revealing scrolls only as far as needed, or centres", () => {
  // A 100 px box scrolled to 0: a row at 40..60 already shows; one at 150..170 needs 70.
  assert.equal(scrollTopFor(0, 100, 40, 20, "nearest"), 0);
  assert.equal(scrollTopFor(0, 100, 150, 20, "nearest"), 70);
  // Above the view: its top at the top. Taller than the view: its top at the top.
  assert.equal(scrollTopFor(200, 100, 150, 20, "nearest"), 150);
  assert.equal(scrollTopFor(0, 100, 150, 300, "nearest"), 150);
  assert.equal(scrollTopFor(0, 100, 150, 20, "center"), 110);
});

test("every class of the menu-column frames is one Tailwind generates, from a file its scan sees", async () => {
  const require = createRequire(path.join(FRONTEND, "package.json"));
  // Tailwind's own build API, as @tailwindcss/postcss runs it (CommonJS; untyped here).
  type Compiler = { build: (candidates: string[]) => string; sources: { base: string; pattern: string; negated: boolean }[] };
  const { compile } = require("@tailwindcss/node") as {
    compile: (css: string, opts: { base: string; onDependency: (p: string) => void }) => Promise<Compiler>;
  };
  const { Scanner } = require("@tailwindcss/oxide") as {
    Scanner: new (opts: { sources: Compiler["sources"] }) => { scan: () => string[] };
  };
  const cssPath = path.join(SRC, "app.css");
  const compiler = await compile(readFileSync(cssPath, "utf8"), { base: path.dirname(cssPath), onDependency() {} });

  const frames = read("components/simulations/SimulationsFrame.tsx");
  const objectInfo = /<PanelCard className="([^"]+)" heading="Selected Object Info">/.exec(read("components/info_box_selected_object/ObjectInfoBoxComponent.tsx"));
  assert.ok(objectInfo, "the Selected Object Info card's classes");
  const chrome = read("components/ui/panelChrome.ts");
  const classes = new Set(
    [
      ...MENU,
      ...COLUMN,
      ...constString(frames, "SIMULATIONS_FRAME_CLASS").split(/\s+/),
      ...constString(frames, "SIMULATIONS_BODY_CLASS").split(/\s+/),
      ...objectInfo[1].split(/\s+/),
      ...constString(chrome, "PANEL_SURFACE").split(/\s+/),
    ].filter(Boolean),
  );

  // One at a time: the build only grows, so a class that adds nothing to it makes no rule.
  let size = compiler.build([]).length;
  const none: string[] = [];
  for (const c of classes) {
    const next = compiler.build([c]).length;
    if (next <= size) none.push(c);
    size = next;
  }
  assert.deepEqual(none, [], "classes Tailwind makes no rule for");

  // The build scans the frontend (Tailwind's automatic source detection, from where it runs) plus
  // the config's `content`; the classes are written in .tsx and .ts files alike.
  const scanner = new Scanner({ sources: [{ base: FRONTEND, pattern: "**/*", negated: false }, ...compiler.sources] });
  const seen = new Set(scanner.scan());
  const unseen = [...classes].filter((c) => !seen.has(c));
  assert.deepEqual(unseen, [], "classes the source scan does not find");
});
