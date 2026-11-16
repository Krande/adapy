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

const grid = (...panels: string[]): import("@/layout/splitTree").GridNode => ({
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

// The docked file browser came back closed on a later visit though the user had not closed
// it: the global panels' state is the user's, and only their open or close may change it.

test("a recorder still subscribed for the context left writes nothing for the one entered", () => {
  withShell(() => {
    enter("one", null);
    reveal("m:files");
    P.recordOpenPanels(isOpen, P.classicContext());
    assert.deepEqual(layoutStore.getState().prefs.globalPanels?.mem?.open, ["m:files"]);

    // An in-place switch to another composed shell, whose start does not open the browser
    // (the flag is one for both shells, as a bound panel's is).
    P.setClassicShell({ id: "desk", modes: { one: { grid: grid("m:a"), open: ["m:a"] } } }, ["one"]);
    P.leaveShell(hide, isOpen);
    P.openStartingPanels(reveal, hide, isOpen);
    const desk = P.classicContext();
    P.recordOpenPanels(isOpen, desk);
    assert.equal(isOpen("m:files"), false);

    // And back. The next shell is installed before the frame's effects move on, so the
    // other shell's recorder can still run, with that shell's panels: it must not write
    // them as this shell's (they would be applied as closed below, for every mode).
    P.setClassicShell(
      { id: "mem", modes: { one: { grid: grid("m:a", "m:b"), open: ["m:a"] }, two: { grid: grid("m:c"), open: ["m:c"] } }, globalPanels: ["m:files"] },
      ["one", "two"],
    );
    P.recordOpenPanels(isOpen, desk);
    assert.deepEqual(layoutStore.getState().prefs.globalPanels?.mem?.open, ["m:files"], "nothing written for mem");
    P.leaveShell(hide, isOpen);
    P.openStartingPanels(reveal, hide, isOpen);
    P.recordOpenPanels(isOpen, P.classicContext());
    assert.equal(isOpen("m:files"), true, "open again, as the user left it");

    // The same for a mode switch: the mode is set before the frame opens its panels.
    const one = P.classicContext();
    M.setShellMode("two");
    hide("m:files"); // whatever the panels are meanwhile
    P.recordOpenPanels(isOpen, one);
    assert.deepEqual(layoutStore.getState().prefs.globalPanels?.mem?.open, ["m:files"]);
    // The user's own close, in the context on screen, is recorded.
    P.recordOpenPanels(isOpen, P.classicContext());
    assert.deepEqual(layoutStore.getState().prefs.globalPanels?.mem?.open, [], "the user closed it: it stays closed");
  });
});

test("opening a model from Storage does not close it when Storage is a global panel", async () => {
  const { CORE_PANEL_IDS, isPanelOpen, reveal: revealCore } = await import("@/layout/panelActions");
  const { collapseStorageOnOpen } = await import("@/components/storage/browser/useProceduralModels");
  const storage = CORE_PANEL_IDS.storage;
  try {
    // Core's own UI: the storage overview gets out of the way of the model opened.
    revealCore(storage);
    collapseStorageOnOpen();
    assert.equal(isPanelOpen(storage), false);
    // A shell that docks it as a global panel: it stays, as the user left it.
    P.setClassicShell({ id: "dock", grid: grid("m:a"), globalPanels: [storage] }, []);
    assert.equal(P.isGlobalPanel(storage), true);
    revealCore(storage);
    collapseStorageOnOpen();
    assert.equal(isPanelOpen(storage), true);
  } finally {
    P.setClassicShell(null);
    layoutStore.setState({ prefs: { v: 1, docs: {} } });
  }
});
