import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  GROUP_DIVIDER,
  GROUP_HEADER,
  PANEL_CHROME,
  PANEL_SURFACE,
  WINDOW_CARD,
  WINDOW_HEADER,
  WINDOW_HEADER_BUTTON,
  groupTabClass,
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
});

test("window and group chrome read tokens that fall back to the classic colours", () => {
  // Tailwind's gray scale, which the classes used before they were tokenised.
  const GRAY = { 100: "#f3f4f6", 400: "#9ca3af", 600: "#4b5563", 700: "#374151", 800: "#1f2937", 900: "#111827" };
  const fallbacks = (s: string) => Object.fromEntries([...s.matchAll(/var\((--ada-[a-z-]+),([^)\]]+\)?)\)/g)].map((m) => [m[1], m[2]]));
  assert.deepEqual(fallbacks(WINDOW_CARD), { "--ada-window-bg": GRAY[900], "--ada-window-border": GRAY[700] });
  assert.deepEqual(fallbacks(WINDOW_HEADER), {
    "--ada-window-header-bg": GRAY[800],
    "--ada-window-header-text": GRAY[100],
    "--ada-window-border": GRAY[700],
  });
  assert.deepEqual(fallbacks(WINDOW_HEADER_BUTTON), {
    "--ada-window-button-border": GRAY[600],
    "--ada-window-button-bg": "rgb(55_65_81/0.6)", // gray-700 at 60 %
  });
  assert.equal(fallbacks(GROUP_HEADER)["--ada-window-header-bg"], GRAY[800], "a group's tab strip is a title bar");
  assert.deepEqual(fallbacks(groupTabClass(true)), { "--ada-tab-active-bg": GRAY[700], "--ada-tab-active-text": "#ffffff" });
  assert.deepEqual(fallbacks(groupTabClass(false)), { "--ada-tab-text": GRAY[400] });
  assert.deepEqual(fallbacks(GROUP_DIVIDER), { "--ada-group-divider": GRAY[700] });
  for (const s of [WINDOW_CARD, WINDOW_HEADER, WINDOW_HEADER_BUTTON, GROUP_HEADER, GROUP_DIVIDER]) {
    assert.doesNotMatch(s, /\b(bg|border|text)-gray-(700|800|900)\b/, "no colour left untokenised");
  }
});
