// A composed shell's mode looks as it was left: the panels open in it come back
// when it is entered again. Global panels (a file browser) are one panel for every
// mode: open or closed, and their docked size, the same in each. And a shell can
// arrange a mode it does not declare (a plugin's).

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const { registerPanel } = await import("@/layout/registry");
const { CLASSIC_ZONES } = await import("@/layout/layout");
const { gridOf } = await import("@/layout/reducers");
const T = await import("@/layout/splitTree");
const P = await import("@/layout/presets/classicPlace");
const M = await import("@/layout/shellModes");
const { layoutStore } = await import("@/layout/layoutStore");

/** A panel whose open state is a flag here, as a bound panel's is in its store. */
const flags = new Map<string, boolean>();
const flag = (id: string) => ({
  isOpen: () => flags.get(id) ?? false,
  setOpen: (v: boolean) => void flags.set(id, v),
  subscribe: () => () => {},
});
const body = () => Promise.resolve({ default: () => null });
for (const id of ["m:files", "m:a", "m:b", "m:c"]) {
  registerPanel({ id, title: id, body, content: body, binding: flag(id), placement: { classic: { zone: CLASSIC_ZONES.menuColumn, order: 1 } } }, "test");
}
const reveal = (id: string) => (flags.set(id, true), true);
const hide = (id: string) => (flags.set(id, false), true);
const isOpen = (id: string) => flags.get(id) ?? false;

const grid = (...panels: string[]): T.GridNode => ({
  kind: "split",
  id: "root",
  dir: "row",
  sizes: [0.2, 0.2, 0.6],
  children: [
    { kind: "group", id: "g-files", panels: ["m:files"], active: "m:files", fixed: { px: 300, min: 200, max: 600 } },
    { kind: "group", id: "g-left", panels, active: panels[0] },
    { kind: "viewport" },
  ],
});

/** What entering a mode does (classic.tsx): leave the last, open the new, record. */
function enter(mode: string, left: string | null) {
  M.setShellMode(mode);
  if (left) P.leaveContext(left, hide, isOpen);
  P.openStartingPanels(reveal, hide, isOpen);
  P.recordOpenPanels(isOpen);
}

function withShell(fn: () => void, extra: Partial<Parameters<typeof P.setClassicShell>[0] & object> = {}) {
  flags.clear();
  M.setShellModes([{ id: "one", label: "One", order: 0 }, { id: "two", label: "Two", order: 1 }], "one");
  P.setClassicShell(
    {
      id: "mem",
      modes: { one: { grid: grid("m:a", "m:b"), open: ["m:a"] }, two: { grid: grid("m:c"), open: ["m:c"] } },
      globalPanels: ["m:files"],
      ...extra,
    },
    ["one", "two"],
  );
  try {
    fn();
  } finally {
    M.setShellModes([]);
    P.setClassicShell(null);
    layoutStore.setState({ prefs: { v: 1, docs: {} } });
  }
}

test("a mode reopens what was open in it, not its starting list", () => {
  withShell(() => {
    enter("one", null);
    assert.deepEqual([isOpen("m:a"), isOpen("m:b")], [true, false], "first visit: the starting list");
    // The user closes a and opens b in mode one.
    hide("m:a");
    reveal("m:b");
    P.recordOpenPanels(isOpen);
    assert.deepEqual(P.classicDoc().open, ["m:b"], "recorded in the mode's document");

    enter("two", "mem/one");
    assert.equal(isOpen("m:c"), true);
    assert.equal(isOpen("m:b"), false, "mode one's panel goes with it");

    enter("one", "mem/two");
    assert.deepEqual([isOpen("m:a"), isOpen("m:b"), isOpen("m:c")], [false, true, false], "as it was left");
  });
});

test("a global panel is one panel for every mode: open, closed and width", () => {
  withShell(() => {
    enter("one", null);
    reveal("m:files");
    P.recordOpenPanels(isOpen);
    // Widened in mode one.
    P.updateClassic((d) => ({ ...d, grid: T.setFixedPx(gridOf(d), "g-files", 420) }));
    P.recordOpenPanels(isOpen);
    assert.deepEqual(layoutStore.getState().prefs.globalPanels?.mem, { open: ["m:files"], px: { "m:files": 420 } });

    enter("two", "mem/one");
    assert.equal(isOpen("m:files"), true, "still open in mode two");
    const g = T.findNode(gridOf(P.classicDoc()), "g-files");
    assert.equal(g && g.kind === "group" ? g.fixed?.px : null, 420, "at the width set in mode one");
    assert.equal(P.classicDoc().open?.includes("m:files"), false, "not part of the mode's own record");

    hide("m:files");
    P.recordOpenPanels(isOpen);
    enter("one", "mem/two");
    assert.equal(isOpen("m:files"), false, "closed in one mode, closed in all");
  });
});

test("a shell's arrangement for a mode it does not declare (a plugin's)", () => {
  M.setShellModes([{ id: "one", label: "One", order: 0 }, { id: "plug", label: "Plugin", order: 1 }], "plug");
  // ComposedLayout folds `layout.modeArrangements` into `modes` by id; here directly.
  P.setClassicShell({ id: "arr", grid: grid("m:a"), modes: { plug: { grid: grid("m:c"), open: ["m:c"] } } }, ["one", "plug"]);
  try {
    assert.ok(T.groupOf(gridOf(P.classicDoc()), "m:c"), "the plugin mode starts from its own grid");
    assert.equal(T.groupOf(gridOf(P.classicDoc()), "m:a"), null);
  } finally {
    M.setShellModes([]);
    P.setClassicShell(null);
    layoutStore.setState({ prefs: { v: 1, docs: {} } });
  }
});

test("a remembered mode survives the document's migration (a reload)", async () => {
  const { migrateLayoutDoc, emptyLayoutDoc } = await import("@/layout/reducers");
  const { CLASSIC_PRESET_ID } = await import("@/layout/layout");
  const { CLASSIC_ZONE_DEFS } = await import("@/layout/presets/classicRules");
  const doc = { ...emptyLayoutDoc(CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, "x"), open: ["m:b", "m:b"] };
  assert.deepEqual(migrateLayoutDoc(JSON.parse(JSON.stringify(doc)), CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, "x").open, ["m:b"]);
  assert.equal(migrateLayoutDoc(emptyLayoutDoc(CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, "x"), CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, "x").open, undefined);
});
