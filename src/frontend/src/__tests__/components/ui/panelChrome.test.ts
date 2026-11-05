import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  PANEL_CHROME,
  PANEL_SURFACE,
  WINDOW_HEADER,
  WINDOW_HEADER_BUTTON,
  panelClass,
  windowHeaderClass,
} from "@/components/ui/panelChrome";

// The strings the panels wrote out themselves before the chrome had one home.
// A class attribute that changes by so much as a space is a DOM difference, so
// these are compared character for character.
const LEGACY_SURFACE =
  "bg-[var(--ada-panel-bg)] border border-[var(--ada-panel-border)] " + "text-[var(--ada-panel-text)] shadow-lg";
const LEGACY_CHROME =
  "bg-[var(--ada-panel-bg)] border border-[var(--ada-panel-border)] " +
  "text-[var(--ada-panel-text)] shadow-lg rounded-md p-2";

test("the surface and the card are the strings the panels used", () => {
  assert.equal(PANEL_SURFACE, LEGACY_SURFACE);
  assert.equal(PANEL_CHROME, LEGACY_CHROME);
});

test("the theme store re-exports the card string, so the facade's PANEL_CHROME is unchanged", () => {
  // Read, not imported: the theme store writes CSS variables onto the document at load.
  const src = readFileSync(new URL("../../../state/themeStore.ts", import.meta.url), "utf8");
  assert.match(src, /export \{PANEL_CHROME\} from "@\/components\/ui\/panelChrome";/);
});

test("panelClass appends a panel's own classes after one space", () => {
  assert.equal(panelClass("card", "min-w-80"), `${LEGACY_CHROME} min-w-80`);
  assert.equal(panelClass("surface", "text-sm flex"), `${LEGACY_SURFACE} text-sm flex`);
  assert.equal(panelClass("card"), LEGACY_CHROME);
  assert.equal(panelClass("surface", ""), LEGACY_SURFACE);
});

test("a window title bar carries the drag handle first, only while draggable", () => {
  assert.equal(windowHeaderClass("viewer-panel-drag-handle"), `viewer-panel-drag-handle cursor-move ${WINDOW_HEADER}`);
  assert.equal(windowHeaderClass(null), WINDOW_HEADER);
  assert.equal(
    WINDOW_HEADER,
    "flex items-center gap-2 bg-gray-800 text-gray-100 px-3 py-2 border-b border-gray-700 shrink-0",
  );
  assert.equal(
    WINDOW_HEADER_BUTTON,
    "shrink-0 inline-flex items-center justify-center w-9 h-9 sm:w-7 sm:h-7 rounded-sm border border-gray-600 " +
      "bg-gray-700/60 text-gray-200 hover:bg-gray-600 hover:border-gray-400 hover:text-white",
  );
});
