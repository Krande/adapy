// Construction's phases and a plugin asking for a mode (1.34.0):
//
// - the chain in work order, the planned phases named but never offered;
// - core's default behaviour for the simulation cells, a UI's declared fields over it;
// - `ctx.modes`: a no-op without modes (core's own UI), a switch where the mode is offered;
// - the Simulations panel's filter in a cell's mode, and the mode a run's result opens in;
// - a simulation provider UI's declared mode, as the registry resolves it.

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const Modes = await import("@/layout/modes");
const { Mode, ModeGroup } = Modes;
const M = await import("@/layout/shellModes");
const P = await import("@/plugins/pluginModes");
const S = await import("@/services/simulations/modeScope");
const { shellModes } = await import("@/layout/ComposedLayout");
const R = await import("@/plugins/registry");

const mode = (id: string, extra: Record<string, unknown> = {}) => ({ id, label: id, order: 0, ...extra });

test("Construction is a chain of phases in work order; Stock is planned", () => {
  assert.deepEqual(
    Modes.coreModesIn(ModeGroup.Construction).map((m) => [m.id, m.status ?? "available"]),
    [
      [Mode.ConstructionStock, "planned"],
      [Mode.ConstructionFabrication, "available"],
      [Mode.ConstructionRobotSim, "available"],
      [Mode.ConstructionInstallationSim, "available"],
    ],
  );
  assert.ok(Modes.isPlannedMode(Mode.ConstructionStock));
  assert.ok(!Modes.isPlannedMode(Mode.ConstructionRobotSim));
  assert.ok(!Modes.isPlannedMode("capacity"), "a plugin's mode is not core's to plan");
});

test("a planned mode a shell lists is never offered", () => {
  const shown = shellModes({
    id: "t",
    label: "T",
    layout: { modes: [{ id: Mode.ConstructionFabrication }, { id: Mode.ConstructionStock }, { id: Mode.ConstructionRobotSim }] },
  } as never);
  assert.deepEqual(shown.map((m) => m.id), [Mode.ConstructionFabrication, Mode.ConstructionRobotSim]);
});

test("the simulation cells' default behaviour, and a UI's declared fields over it", () => {
  for (const id of [Mode.ConstructionRobotSim, Mode.ConstructionInstallationSim]) {
    const b = Modes.coreModeDef(id)!.behaviour!;
    assert.equal(b.builder, "away", id);
    assert.equal(b.sceneColor, "own", id);
    assert.equal(b.deformation, false, id);
    assert.equal(b.simulations?.domain, Modes.ROBOTICS_SIMULATION_DOMAIN, id);
    assert.equal(b.simulations?.ownProviders, true, id);
    assert.ok((b.simulations?.empty ?? "").length > 10, `${id}: an empty state`);
  }
  assert.match(Modes.coreModeDef(Mode.ConstructionInstallationSim)!.behaviour!.simulations!.empty!, /no installation simulation provider yet/i);
  const merged = Modes.withCoreBehaviour(Mode.ConstructionRobotSim, { builder: "compare" })!;
  assert.equal(merged.builder, "compare", "the UI's field wins");
  assert.equal(merged.sceneColor, "own", "core's default for the rest");
  assert.equal(Modes.withCoreBehaviour(Mode.TopologyOutput, undefined), undefined, "a mode without a default keeps none");

  const [robot] = shellModes({ id: "t", label: "T", layout: { modes: [{ id: Mode.ConstructionRobotSim }] } } as never);
  M.setShellModes([robot], robot.id);
  try {
    assert.equal(M.builderPresence(), "away");
    assert.equal(M.modeOwnsSceneColor(M.activeModeDef()), true);
    assert.equal(M.modeShowsDeformation(M.activeModeDef()), false);
  } finally {
    M.setShellModes([]);
  }
});

test("ctx.modes: nothing to do in core's own UI, which has no modes", () => {
  M.setShellModes([]);
  const modes = P.makePluginModes();
  assert.equal(modes.active, null);
  assert.equal(modes.offered(Mode.ConstructionRobotSim), false);
  assert.equal(modes.activate(Mode.ConstructionRobotSim, { reason: "replay" }), false);
});

test("ctx.modes: activate switches to an offered mode, and subscribers hear the switch once", () => {
  M.setShellModes([mode(Mode.TopologyOutput), mode(Mode.ConstructionRobotSim)], Mode.TopologyOutput);
  const lines: string[] = [];
  const modes = P.makePluginModes((l) => lines.push(l));
  const seen: (string | null)[] = [];
  const off = modes.subscribe((id) => seen.push(id));
  try {
    assert.equal(modes.offered(Mode.ConstructionRobotSim), true);
    assert.equal(modes.offered(Mode.ConstructionInstallationSim), false, "not offered by this UI");
    assert.equal(modes.activate(Mode.ConstructionInstallationSim), false);
    assert.equal(modes.active, Mode.TopologyOutput, "nothing changed");
    assert.equal(modes.activate(Mode.ConstructionRobotSim, { reason: "replay of run 1" }), true);
    assert.equal(modes.active, Mode.ConstructionRobotSim);
    assert.equal(modes.activate(Mode.ConstructionRobotSim), true, "already there");
    M.setShellModeBadge(Mode.TopologyOutput, 2); // a store change that is no switch
    assert.deepEqual(seen, [Mode.ConstructionRobotSim]);
    assert.match(lines[0], /construction-robot-sim.*replay of run 1/);
  } finally {
    off();
    M.setShellModeBadge(Mode.TopologyOutput, null);
    M.setShellModes([]);
  }
});

test("ctx.modes: a planned mode is never offered, even when a UI installs it by hand", () => {
  M.setShellModes([mode(Mode.TopologyOutput), mode(Mode.ConstructionStock)], Mode.TopologyOutput);
  try {
    assert.equal(P.modeOffered(Mode.ConstructionStock), false);
    assert.equal(P.activateMode(Mode.ConstructionStock), false);
  } finally {
    M.setShellModes([]);
  }
});

test("the Simulations panel in a cell's mode lists that cell's providers only", () => {
  const robotMode = { id: Mode.ConstructionRobotSim, behaviour: Modes.coreModeDef(Mode.ConstructionRobotSim)!.behaviour };
  const installMode = { id: Mode.ConstructionInstallationSim, behaviour: Modes.coreModeDef(Mode.ConstructionInstallationSim)!.behaviour };
  const robot = { id: "robot", domain: "robotics" };
  const fea = { id: "fea", domain: "fea" };
  const declared = (p: { id: string }) => (p.id === "robot" ? Mode.ConstructionRobotSim : null);
  assert.ok(S.providerShownInMode(robot, robotMode, declared));
  assert.ok(!S.providerShownInMode(fea, robotMode, declared), "another domain");
  assert.ok(!S.providerShownInMode(robot, installMode, declared), "another cell's provider");
  assert.ok(S.providerShownInMode(fea, null, declared), "no mode: everything");
  assert.ok(S.providerShownInMode(fea, { id: Mode.FemResults }, declared), "a mode without a filter: everything");
  assert.match(S.simulationsFilterFor(installMode)!.empty!, /installation/i);
});

test("a run's result opens in its provider's mode, where the UI offers it", () => {
  const offered = (id: string) => id === Mode.ConstructionRobotSim;
  assert.equal(S.modeToOpenIn(Mode.ConstructionRobotSim, Mode.TopologyOutput, offered), Mode.ConstructionRobotSim);
  assert.equal(S.modeToOpenIn(Mode.ConstructionRobotSim, Mode.ConstructionRobotSim, offered), null, "already there");
  assert.equal(S.modeToOpenIn(Mode.ConstructionInstallationSim, Mode.TopologyOutput, offered), null, "not offered: stay");
  assert.equal(S.modeToOpenIn(null, Mode.TopologyOutput, offered), null, "none declared: stay");
});

test("a simulation provider UI declares the mode its runs belong to", () => {
  R.registerPlugin({
    id: "cell-sim-test",
    version: "1.0.0",
    coreApiRange: ">=1.34 <2.0",
    simulationProviders: [
      { id: "form", providerId: "cell", renderParameters: () => null },
      { id: "replay", domain: "robotics", mode: Mode.ConstructionInstallationSim, openResult: () => undefined },
    ],
  });
  const ctx = { pluginId: "" } as never;
  assert.equal(R.resolveSimulationProviderUi(ctx, { id: "cell", domain: "robotics" }).mode, Mode.ConstructionInstallationSim);
  assert.equal(R.resolveSimulationProviderUi(ctx, { id: "cell", domain: "fea" }).mode, null);
});

test("the API versions name the addition", () => {
  const reg = readFileSync(new URL("../../plugins/registry.ts", import.meta.url), "utf8");
  const [maj, min] = /PLUGIN_API_VERSION = "([\d.]+)"/.exec(reg)![1].split(".").map(Number);
  assert.ok(maj > 1 || min >= 34);
  assert.match(reg, /1\.34\.0 — Construction's phases/);
});
