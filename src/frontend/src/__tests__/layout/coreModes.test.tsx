// Core's modes and what each UI assigns them (layout/modes, layout/shellModes):
//
// - the catalogue: typed constants, grouped under headings;
// - core reacts to a mode only through the active mode's declared behaviour: core's own UI (no
//   modes) never gets a mode's behaviour, a mode that declares none gets the defaults;
// - a UI's switching rules: `?mode=`, the remembered mode, a fallback, aliases for old ids;
// - the Desktop UI offers core's modes, its old ids as aliases;
// - a mode's scene scope is reversible, and lets go of what the user changes by hand.

import assert from "node:assert/strict";
import { test } from "node:test";

const { CORE_MODES, MODE_GROUPS, Mode, ModeGroup, coreModeDef, isCoreMode } = await import("@/layout/modes");
const M = await import("@/layout/shellModes");
const { initialShellMode } = M;
const D = await import("@/layout/desktop/desktopShell");
const S = await import("@/layout/sceneScopeRules");

const mode = (id: string, behaviour?: Record<string, unknown>, extra: Record<string, unknown> = {}) => ({
  id,
  label: id,
  order: 0,
  ...(behaviour ? { behaviour } : {}),
  ...extra,
});

test("the catalogue: every core mode under a heading, in work order, with the Docked UI's names", () => {
  assert.deepEqual(
    CORE_MODES.map((m) => m.id),
    [
      "topology-input",
      "topology-output",
      "fem-model",
      "fem-results",
      "construction-stock",
      "construction-fabrication",
      "construction-robot-sim",
      "construction-installation-sim",
    ],
  );
  assert.deepEqual(
    CORE_MODES.map((m) => m.label),
    ["Input", "Output", "Model", "Results", "Stock", "Fabrication-cad", "Robot-sim (weld)", "Installation-sim"],
  );
  const orders = CORE_MODES.map((m) => m.order);
  assert.deepEqual(orders, [...orders].sort((a, b) => a - b), "listed in work order");
  for (const m of CORE_MODES) {
    assert.ok(MODE_GROUPS.some((g) => g.id === m.group), `${m.id}: a heading`);
    assert.ok(m.hint.length > 10, `${m.id}: a hint`);
  }
  assert.equal(coreModeDef(Mode.FemResults)?.group, ModeGroup.Fem);
  assert.ok(isCoreMode(Mode.TopologyInput));
  assert.ok(!isCoreMode("capacity"), "a plugin's mode is not core's");
  assert.equal(coreModeDef("capacity"), undefined);
});

test("core's own UI has no modes: the builder is always edited there, whatever is drawn", () => {
  M.setShellModes([]);
  assert.equal(M.activeShellMode(), null);
  assert.equal(M.builderPresence(), "edit");
  assert.equal(M.modeOwnsSceneColor(M.activeModeDef()), false);
  assert.equal(M.modeShowsDeformation(M.activeModeDef()), true);
});

test("a mode's behaviour is what its UI declared, and the defaults where it declared none", () => {
  M.setShellModes(
    [mode(Mode.TopologyInput, { builder: "edit" }), mode(Mode.FemResults, { builder: "away", sceneColor: "results" }), mode(Mode.TopologyOutput)],
    Mode.TopologyInput,
  );
  try {
    assert.equal(M.builderPresence(), "edit");
    M.setShellMode(Mode.FemResults);
    assert.equal(M.builderPresence(), "away");
    assert.equal(M.modeOwnsSceneColor(M.activeModeDef()), false);
    M.setShellMode(Mode.TopologyOutput);
    assert.equal(M.builderPresence(), "compare", "a mode that declares none follows what is drawn, as since 1.31.0");
  } finally {
    M.setShellModes([]);
  }
});

test("a declared behaviour wins over a mode's older flags", () => {
  const owns = mode("a", { sceneColor: "results", deformation: true }, { ownsSceneColor: true, showsDeformation: false });
  assert.equal(M.modeOwnsSceneColor(owns as never), false);
  assert.equal(M.modeShowsDeformation(owns as never), true);
  const flagsOnly = mode("b", undefined, { ownsSceneColor: true, showsDeformation: false });
  assert.equal(M.modeOwnsSceneColor(flagsOnly as never), true);
  assert.equal(M.modeShowsDeformation(flagsOnly as never), false);
});

test("the state is one store: a switch is seen by its subscribers", () => {
  M.setShellModes([mode(Mode.TopologyInput), mode(Mode.FemResults)], Mode.TopologyInput);
  let seen = 0;
  const off = M.subscribeShellModes(() => seen++);
  try {
    const v = M.shellModesVersion();
    assert.ok(M.setShellMode(Mode.FemResults));
    assert.equal(M.modeStore.getState().active, Mode.FemResults);
    assert.ok(M.shellModesVersion() > v);
    assert.equal(seen, 1);
    assert.equal(M.setShellMode("not-offered"), false);
  } finally {
    off();
    M.setShellModes([]);
  }
});

test("switching rules: the link, the memory, the default, and old ids as aliases", () => {
  const modes = [{ id: Mode.TopologyInput }, { id: Mode.TopologyOutput }, { id: Mode.FemResults }];
  assert.equal(initialShellMode(modes, Mode.FemResults, Mode.TopologyInput, Mode.TopologyOutput), Mode.FemResults);
  assert.equal(initialShellMode(modes, null, Mode.TopologyInput, Mode.TopologyOutput), Mode.TopologyInput);
  assert.equal(initialShellMode(modes, "nope", null, Mode.TopologyOutput), Mode.TopologyOutput);
  const aliases = { results: Mode.FemResults, build: Mode.TopologyInput };
  assert.equal(initialShellMode(modes, "results", null, undefined, { aliases }), Mode.FemResults, "an old link");
  assert.equal(initialShellMode(modes, null, "build", undefined, { aliases }), Mode.TopologyInput, "an old remembered mode");
  assert.equal(initialShellMode(modes, Mode.FemResults, Mode.TopologyInput, Mode.TopologyOutput, { urlParam: false }), Mode.TopologyInput);
  assert.equal(initialShellMode(modes, null, Mode.TopologyInput, Mode.TopologyOutput, { remember: false }), Mode.TopologyOutput);
});

test("the Desktop UI offers core's modes, under its own labels, its old ids as aliases", async () => {
  const spec = D.desktopUiShellSpec();
  const modes = spec.layout!.modes!;
  assert.deepEqual(
    modes.map((m) => [m.id, m.label, m.behaviour?.builder]),
    [
      [Mode.TopologyOutput, "Model", "compare"],
      [Mode.FemResults, "Results", "compare"],
      [Mode.TopologyInput, "Build", "edit"],
      [Mode.ConstructionRobotSim, undefined, undefined],
      [Mode.ConstructionInstallationSim, undefined, undefined],
    ],
  );
  await import("../fixtures/panelRealm");
  const shown = (await import("@/layout/ComposedLayout")).shellModes(spec);
  const robot = shown.find((m) => m.id === Mode.ConstructionRobotSim)!;
  assert.equal(robot.label, "Robot-sim (weld)", "core's label");
  assert.equal(robot.behaviour?.builder, "away", "core's default behaviour");
  const { CORE_PANEL_IDS } = await import("@/layout/corePanelIds");
  assert.ok(robot.open?.includes(CORE_PANEL_IDS.simulation), "the Simulations panel opens there");
  assert.ok(robot.open?.includes(CORE_PANEL_IDS.storage), "and storage");
  for (const m of modes) assert.ok(isCoreMode(m.id), `${m.id} is one of core's`);
  assert.equal(spec.layout!.defaultMode, Mode.TopologyOutput);
  assert.deepEqual(spec.layout!.modeSwitching?.aliases, { model: Mode.TopologyOutput, results: Mode.FemResults, build: Mode.TopologyInput });
});

// --- the scene scope -----------------------------------------------------------------------------

const groups = (entries: Record<string, boolean>) => new Map(Object.entries(entries).map(([k, v]) => [k, { visible: v }]));
const vis = (g: Map<string, { visible: boolean }>) => Object.fromEntries([...g].map(([k, v]) => [k, v.visible]));
const onlyCad = { draws: (m: { name: string }) => m.name.endsWith(".glb") };

test("a scoped mode hides what it does not draw, keeps what the user hid, and puts it all back after", () => {
  const g = groups({ "a.glb": true, "b.glb": false, "deck.sin": true });
  const mem = S.newSceneScopeMemory();
  assert.ok(S.applyModeSceneScope(Mode.TopologyOutput, onlyCad, g, mem));
  assert.deepEqual(vis(g), { "a.glb": true, "b.glb": false, "deck.sin": false });
  assert.ok(S.applyModeSceneScope(Mode.FemResults, null, g, mem), "a mode without a scope restores");
  assert.deepEqual(vis(g), { "a.glb": true, "b.glb": false, "deck.sin": true });
  assert.equal(mem.before.size, 0);
});

test("what the user changes by hand while scoped is theirs, until the mode changes", () => {
  const g = groups({ "a.glb": true, "deck.sin": true });
  const mem = S.newSceneScopeMemory();
  S.applyModeSceneScope(Mode.TopologyOutput, onlyCad, g, mem);
  g.get("deck.sin")!.visible = true; // shown by hand
  S.applyModeSceneScope(Mode.TopologyOutput, onlyCad, g, mem);
  assert.equal(g.get("deck.sin")!.visible, true, "not hidden again in the same mode");
  S.applyModeSceneScope(Mode.FemModel, onlyCad, g, mem);
  assert.equal(g.get("deck.sin")!.visible, false, "another scoped mode applies its rule again");
});

test("a model loaded while scoped is scoped as it arrives; one unloaded is forgotten", () => {
  const g = groups({ "a.glb": true });
  const mem = S.newSceneScopeMemory();
  S.applyModeSceneScope(Mode.TopologyOutput, onlyCad, g, mem);
  g.set("late.sin", { visible: true });
  S.applyModeSceneScope(Mode.TopologyOutput, onlyCad, g, mem);
  assert.equal(g.get("late.sin")!.visible, false);
  g.delete("late.sin");
  S.applyModeSceneScope(Mode.TopologyOutput, onlyCad, g, mem);
  assert.ok(!mem.before.has("late.sin"));
});

test("without a scope nothing is ever touched (core's own UI, a shell that scopes its scene itself)", () => {
  const g = groups({ "a.glb": false, "deck.sin": true });
  const mem = S.newSceneScopeMemory();
  assert.equal(S.applyModeSceneScope(null, null, g, mem), false);
  assert.equal(S.applyModeSceneScope(Mode.TopologyInput, undefined, g, mem), false);
  assert.deepEqual(vis(g), { "a.glb": false, "deck.sin": true });
});
