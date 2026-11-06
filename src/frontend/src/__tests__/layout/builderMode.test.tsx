// The Build mode: the builder's core commands and their reasons, the `build` mode
// toolbar, a mode that `requires` a model, and the Desktop UI's use of them.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const C = await import("@/layout/commands");
const M = await import("@/layout/shellModes");
const R = await import("@/layout/modeRequirements");
const D = await import("@/layout/desktop/desktopShell");
const { desktopMenus, menuCommandIds } = await import("@/layout/desktop/desktopMenus");
const { CORE_COMMAND_IDS: ID } = await import("@/layout/coreCommandIds");
const { CORE_COMMAND_ICONS } = await import("@/layout/commandIcons");
const { registerCoreCommands } = await import("@/layout/coreCommands");
const T = await import("@/layout/ModeToolbars");
const { ShellModeSwitcher, ShellModeToolbar } = await import("@/layout/ShellModeSwitcher");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { CORE_PANEL_IDS } = await import("@/layout/corePanelIds");
const { readFileSync } = await import("node:fs");

const html = (el: React.ReactElement) => renderToStaticMarkup(React.createElement(AdaViewerProvider, null, el));
const reason = (id: string) => C.getCommand(id)!.disabledReason?.() ?? null;

const BUILDER_COMMANDS = [
  ID.builderUndo, ID.builderRedo, ID.builderCompile, ID.builderRecompile,
  ID.builderCompileBrowser, ID.builderCommit, ID.builderClose,
];

function setup(): void {
  C.resetCommands();
  registerCoreCommands();
  useCellBuilderStore.getState().close();
}

test("every builder command is registered, with an icon, and refused without a model", () => {
  setup();
  for (const id of BUILDER_COMMANDS) {
    assert.ok(C.getCommand(id), `${id} is registered`);
    assert.ok(CORE_COMMAND_ICONS[id], `${id} has an icon`);
    assert.ok(reason(id), `${id} says why it cannot run`);
  }
  assert.equal(reason(ID.builderCompile), "No procedural model is open");
  assert.equal(reason(ID.builderClose), "No procedural model is open");
  C.resetCommands();
});

test("a model read off the scene is read-only: its commands say so", () => {
  setup();
  useCellBuilderStore.setState({ cells: { c1: {} as never } });
  assert.equal(R.builderLoaded(), true);
  assert.equal(reason(ID.builderCommit), "This model was read off the scene, not opened from storage");
  assert.equal(reason(ID.builderCompile), "This model was read off the scene, not opened from storage");
  useCellBuilderStore.setState({ cells: {} });
  C.resetCommands();
});

test("the build toolbar draws the builder's buttons, greyed with their reasons", () => {
  setup();
  const out = html(React.createElement(T.BuilderToolbar));
  assert.match(out, /data-mode-toolbar="build"/);
  assert.match(out, /No procedural model/);
  for (const id of [ID.builderUndo, ID.builderRedo, ID.builderCompile, ID.builderCompileBrowser, ID.builderCommit, ID.builderClose]) {
    assert.match(out, new RegExp(`data-command="${id}"`));
  }
  assert.doesNotMatch(out, new RegExp(`data-command="${ID.builderRecompile}"`), "force recompile is a menu item, not a button");
  assert.match(out, /disabled=""/);
  assert.match(out, /title="Compile preview \(No procedural model is open\)"/);
  C.resetCommands();
});

test("the facade names the toolbar and the build toolbar is a core toolbar id", () => {
  assert.ok((M.CORE_MODE_TOOLBAR_IDS as readonly string[]).includes("build"));
  assert.ok(T.CORE_MODE_TOOLBARS.build);
  const facade = readFileSync(new URL("../../viewer-core/layout.ts", import.meta.url), "utf8");
  assert.match(facade, /\bBuilderToolbar\b[^;]*from "@\/layout\/ModeToolbars"/);
});

test("a mode that requires a builder model is offered only while one is open", () => {
  setup();
  M.setShellModes([
    { id: "model", label: "Model", order: 0 },
    { id: "build", label: "Build", order: 1, requires: "builder", toolbars: ["build"] },
  ], "model");
  const names = () => C.listCommands().filter((c) => c.id.startsWith("mode:")).map((c) => c.id);
  assert.deepEqual(names(), ["mode:model"]);
  const bar = () => html(React.createElement(ShellModeSwitcher, {}));
  assert.doesNotMatch(bar(), /Build/);

  useCellBuilderStore.setState({ cells: { c1: {} as never } });
  assert.deepEqual(names(), ["mode:model", "mode:build"]);
  assert.match(bar(), /Build/);
  M.setShellMode("build");
  assert.match(html(React.createElement(ShellModeToolbar)), /data-mode-toolbar="build"/);

  // The requirement going away is reported (the layout then switches to the first mode on offer).
  let told = 0;
  const off = R.subscribeModeRequirements(() => told++);
  useCellBuilderStore.setState({ cells: {} });
  off();
  assert.equal(told, 1);
  assert.equal(R.modeAvailable(M.listShellModes().find((m) => m.id === "build")!), false);
  M.setShellModes([]);
  C.resetCommands();
});

test("the Desktop UI has a Build mode docking the builder, with its toolbar, menu and overlays", () => {
  const spec = D.desktopUiShellSpec();
  const build = spec.layout!.modes!.find((m) => m.id === "build")!;
  assert.ok(build, "a Build mode");
  assert.equal(build.requires, "builder");
  assert.deepEqual(build.toolbars, ["build", "section"]);
  assert.ok(build.open!.includes(CORE_PANEL_IDS.cellbuilder));
  const groups: { id: string; panels: string[]; active?: string }[] = [];
  const walk = (n: { kind: string; children?: unknown[]; id?: string; panels?: string[]; active?: string }) => {
    if (n.kind === "group") groups.push(n as never);
    for (const c of n.children ?? []) walk(c as never);
  };
  walk(build.grid as never);
  const left = groups.find((g) => g.id === D.DESKTOP_GROUPS.left)!;
  assert.equal(left.active, CORE_PANEL_IDS.cellbuilder);
  assert.ok(left.panels.includes(CORE_PANEL_IDS.tree));
  assert.ok(groups.some((g) => g.panels.includes(CORE_PANEL_IDS.equipmentAdmin)));
  // The toolbar carries compile and commit, so the docked panel leaves its footer out in this mode only.
  const opts = spec.panels![CORE_PANEL_IDS.cellbuilder].options as (m: string | null) => Record<string, unknown>;
  assert.deepEqual(opts("build"), { showFooter: false, showUndoRedo: false });
  assert.deepEqual(opts("model"), {});
  assert.equal(spec.chrome!.overlays!.builder, true);
  // The other modes stay as they were.
  assert.equal(spec.layout!.modes!.find((m) => m.id === "model")!.requires, undefined);
});

test("the Build menu names the builder's commands, only where the shell has the mode", () => {
  const inputs = { workspaces: [], shells: [], themes: [] };
  const withMode = desktopMenus({ ...inputs, modes: [{ id: "model" }, { id: "build" }] });
  const menu = withMode.find((m) => m.label === "Build")!;
  assert.ok(menu);
  const ids = menuCommandIds([menu]);
  for (const id of BUILDER_COMMANDS) assert.ok(ids.includes(id), id);
  assert.ok(!desktopMenus({ ...inputs, modes: [{ id: "model" }] }).some((m) => m.label === "Build"));
});

test("the API version moved with the Build mode", () => {
  const index = readFileSync(new URL("../../viewer-core/index.ts", import.meta.url), "utf8");
  assert.match(index, /VIEWER_CORE_API_VERSION = "1\.18\.0"/);
  const reg = readFileSync(new URL("../../plugins/registry.ts", import.meta.url), "utf8");
  assert.match(reg, /PLUGIN_API_VERSION = "1\.18\.0"/);
});
