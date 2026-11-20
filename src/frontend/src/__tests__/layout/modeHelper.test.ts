// The classic UI's mode helper: which headings are lit, how the comparison dropdown maps onto the
// builder's view state, its command and key, its remembered on / off, and that only core's own UI has it.

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

await import("../fixtures/panelRealm");

const R = await import("@/layout/modeHelperRules");
const { useModeHelperStore, MODE_HELPER_STORAGE_KEY } = await import("@/state/modeHelperStore");
const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { coreCommandSpecs } = await import("@/layout/coreCommands");
const { CORE_SHORTCUTS } = await import("@/layout/coreShortcutTable");
const { RESERVED_SHORTCUTS, listShortcuts, parseShortcut, resetShortcuts } = await import("@/layout/shortcuts");
const { commandChord } = await import("@/layout/commands");
const { corePanelSpecs, CORE_PANEL_IDS } = await import("@/layout/corePanels");
const { setClassicShell } = await import("@/layout/presets/classicShell");
const { homeOf, homeLabel } = await import("@/layout/presets/classicPlace");

const none: import("@/layout/modeHelperRules").HelperInputs = {
  builderLoaded: false,
  simulationLoaded: false,
  detailLoaded: false,
  manifest: null,
  sessionActive: false,
  femHasConcepts: false,
};
const lit = (i: Partial<typeof none>) => [...R.activeModes({ ...none, ...i })].sort();

afterEach(() => {
  setClassicShell(null);
  useModeHelperStore.setState({ on: false });
});

test("nothing loaded lights no heading", () => {
  assert.deepEqual(lit({}), []);
});

test("a procedural model lights Topology", () => {
  assert.deepEqual(lit({ builderLoaded: true }), ["topology"]);
});

test("an FE model or a result session lights FEM", () => {
  assert.deepEqual(lit({ manifest: { any: 1 } }), ["fem"]);
  assert.deepEqual(lit({ femHasConcepts: true }), ["fem"]);
  assert.deepEqual(lit({ sessionActive: true }), ["fem"]);
});

test("a loaded detail result lights Construction; the view merely selected, with nothing compiled, does not", () => {
  assert.deepEqual(lit({ builderLoaded: true, detailLoaded: true }), ["construction", "topology"]);
});

test("several headings are lit at once; the builder's compiled simulation result is the FE model drawn", () => {
  assert.deepEqual(lit({ builderLoaded: true, simulationLoaded: true }), ["fem", "topology"]);
  assert.deepEqual(lit({ builderLoaded: true, detailLoaded: true, sessionActive: true }), ["construction", "fem", "topology"]);
});

test("the headings are the three kinds, in order", () => {
  assert.deepEqual(R.HELPER_MODES.map((m) => m.label), ["Topology", "FEM", "Construction"]);
});

// The dropdown and the builder's two flags (the slice's own actions, as `builderSuperimpose` / `builderSideBySide` use).
function fakeBuilder(repMode: "topology" | "simulation" | "detail" = "simulation") {
  const s = {
    repMode,
    superimpose: false,
    sideBySide: false,
    calls: [] as string[],
    async setSuperimpose(on: boolean) {
      s.calls.push(`superimpose:${on}`);
      s.superimpose = on;
    },
    setSideBySide(on: boolean) {
      s.calls.push(`beside:${on}`);
      s.sideBySide = on;
    },
    async setRepMode(m: "simulation") {
      s.calls.push(`rep:${m}`);
      s.repMode = m;
    },
  };
  return s;
}

test("the dropdown shows the builder's flags: beside wins, then overlay, else off", () => {
  assert.equal(R.comparisonOf({ superimpose: false, sideBySide: false }), "off");
  assert.equal(R.comparisonOf({ superimpose: true, sideBySide: false }), "overlay");
  assert.equal(R.comparisonOf({ superimpose: false, sideBySide: true }), "side-by-side");
  assert.equal(R.comparisonOf({ superimpose: true, sideBySide: true }), "side-by-side");
  assert.deepEqual(R.COMPARISONS.map((c) => c.label), ["Side by side", "Overlay", "Off"]);
});

test("each option turns the other comparison off, so exactly one is on", async () => {
  const b = fakeBuilder();
  await R.applyComparison("overlay", b);
  assert.deepEqual([b.superimpose, b.sideBySide], [true, false]);
  await R.applyComparison("side-by-side", b);
  assert.deepEqual([b.superimpose, b.sideBySide], [false, true]);
  await R.applyComparison("off", b);
  assert.deepEqual([b.superimpose, b.sideBySide], [false, false]);
  assert.equal(R.comparisonOf(b), "off");
});

test("from the plain topology view, side by side opens the simulation view to compare with", async () => {
  const b = fakeBuilder("topology");
  await R.applyComparison("side-by-side", b);
  assert.equal(b.sideBySide, true);
  assert.equal(b.repMode, "simulation");
  const o = fakeBuilder("simulation");
  await R.applyComparison("side-by-side", o);
  assert.ok(!o.calls.includes("rep:simulation"), "already on a result: the view is left alone");
});

test("the dropdown reads the real store's flags", () => {
  useCellBuilderStore.setState({ superimpose: true, sideBySide: false });
  assert.equal(R.comparisonOf(useCellBuilderStore.getState()), "overlay");
  useCellBuilderStore.setState({ superimpose: false, sideBySide: false });
});

test("the command, its key and the dialog line are registered", () => {
  const spec = coreCommandSpecs().find((s) => s.id === R.MODE_HELPER_COMMAND_ID);
  assert.ok(spec, "a core command");
  assert.equal(spec.label, "Mode helper");
  assert.equal(spec.shortcut, "core:toggle-mode-helper");
  const def = CORE_SHORTCUTS.find((d) => d.id === "core:toggle-mode-helper");
  assert.equal(def?.keys, "Shift+B");
  assert.ok(RESERVED_SHORTCUTS.includes("Shift+B"), "plugins are refused the chord");
  assert.equal(commandChord(spec), "Shift+B");
  // Free: no other core entry (the builder's own keys included) is bound to it.
  const chord = (k: string) => JSON.stringify(parseShortcut(k));
  const same = CORE_SHORTCUTS.filter((d) => chord(d.keys as string) === chord("Shift+B"));
  assert.deepEqual(same.map((d) => d.id), ["core:toggle-mode-helper"]);
  resetShortcuts();
  assert.ok(listShortcuts().some((s) => s.id === "core:toggle-mode-helper" && s.keys === "Shift+B"));
});

test("the command toggles the helper and shows it checked; off by default", () => {
  const spec = coreCommandSpecs().find((s) => s.id === R.MODE_HELPER_COMMAND_ID)!;
  assert.equal(useModeHelperStore.getState().on, false);
  assert.equal(spec.checked?.(), false);
  spec.run();
  assert.equal(useModeHelperStore.getState().on, true);
  assert.equal(spec.checked?.(), true);
  spec.run();
  assert.equal(useModeHelperStore.getState().on, false);
});

test("on / off is remembered in this browser, and blocked storage does not break it", () => {
  useModeHelperStore.getState().setOn(true);
  const kept = JSON.parse(window.localStorage.getItem(MODE_HELPER_STORAGE_KEY)!);
  assert.equal(kept.state.on, true);
  const real = Object.getOwnPropertyDescriptor(window, "localStorage")!;
  Object.defineProperty(window, "localStorage", {
    configurable: true,
    get() {
      throw new Error("blocked");
    },
  });
  try {
    assert.doesNotThrow(() => useModeHelperStore.getState().setOn(false));
    assert.equal(useModeHelperStore.getState().on, false);
  } finally {
    Object.defineProperty(window, "localStorage", real);
  }
});

test("only core's own UI has it: a composed shell gets neither the command, the key nor the panel", () => {
  const spec = coreCommandSpecs().find((s) => s.id === R.MODE_HELPER_COMMAND_ID)!;
  const panel = corePanelSpecs().find((p) => p.id === CORE_PANEL_IDS.modeHelper)!;
  assert.equal(spec.when?.(), true);
  assert.equal(panel.available?.({} as never), true);
  setClassicShell({ id: "some-shell" });
  assert.equal(R.modeHelperOffered(), false);
  assert.equal(spec.when?.(), false);
  assert.equal(panel.available?.({} as never), false);
  assert.equal(R.toggleModeHelper(), false, "the key is not ours there");
  assert.equal(useModeHelperStore.getState().on, false);
});

test("the panel is a classic column panel the layout can float and dock, whose way home is the top", () => {
  const panel = corePanelSpecs().find((p) => p.id === CORE_PANEL_IDS.modeHelper)!;
  assert.equal(homeOf(panel), "column");
  assert.equal(panel.toolbar, undefined, "no top-bar button");
  assert.equal(panel.placement?.classic?.startFloated, undefined, "it starts at home, not in a window");
  assert.equal(homeLabel(panel, "column"), "Back to the top");
  assert.equal(homeLabel({ id: "core:scene" }, "column"), "Back to the panel column");
});
