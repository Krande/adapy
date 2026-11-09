// The built-in Desktop UI: registered next to the classic UI and offered by the
// switcher, its menus naming only commands that exist, its grids already
// normal, and its chrome rendering.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const U = await import("@/plugins/uiShells");
const D = await import("@/layout/desktop/desktopShell");
const { desktopMenus, menuCommandIds } = await import("@/layout/desktop/desktopMenus");
const { registerCorePanels } = await import("@/layout/corePanels");
const { getPanel } = await import("@/layout/registry");
const C = await import("@/layout/commands");
const M = await import("@/layout/shellModes");
const { CORE_COMMAND_IDS } = await import("@/layout/coreCommandIds");
const { registerCoreCommands } = await import("@/layout/coreCommands");
const { REOPEN_WINDOWS_COMMAND } = await import("@/layout/windows");
const { gridGroups, gridPanels, normaliseGrid } = await import("@/layout/splitTree");
const { offeredMenuItems } = await import("@/layout/CommandPalette");
const { THEME_PRESETS } = await import("@/state/themeStore");
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { UiShellSwitcher } = await import("@/plugins/UiShellSwitcher");
const DesktopHeader = (await import("@/layout/desktop/DesktopHeader")).default;
const { StatusBar, connectionLabel } = await import("@/layout/StatusBar");
const DesktopPageHeader = (await import("@/layout/desktop/DesktopPageHeader")).default;

registerCorePanels();

/** The classic UI's registration without `@/app`, then the Desktop UI's. */
function registerShells(): void {
  U.resetUiShells();
  U.registerUiShell({ id: U.CORE_UI_SHELL_ID, label: "Classic", order: 0, load: async () => ({ default: () => null }) }, U.CORE_UI_SHELL_ID);
  D.registerDesktopUiShell();
}

const spec = D.desktopUiShellSpec();
const modes = (spec.layout?.modes ?? []).map((m, i) => ({ id: m.id, label: m.label, order: i }));
const html = (el: React.ReactElement) => renderToStaticMarkup(React.createElement(AdaViewerProvider, null, el));

test("the Desktop UI registers next to the classic one, composed, and the classic stays the default", () => {
  registerShells();
  D.registerDesktopUiShell(); // idempotent
  assert.deepEqual(U.listUiShells().map((s) => s.id), [U.CORE_UI_SHELL_ID, D.DESKTOP_UI_SHELL_ID]);
  const s = U.getUiShell(D.DESKTOP_UI_SHELL_ID)!;
  assert.equal(s.label, "Desktop");
  assert.equal(s.pluginId, "core");
  assert.equal(U.isComposedShell(s), true);
  assert.equal(U.resolveUiShell({ search: "", stored: null, runtimeDefault: null, buildDefault: null }).id, U.CORE_UI_SHELL_ID);
  assert.deepEqual(U.resolveUiShell({ search: `?ui=${D.DESKTOP_UI_SHELL_ID}`, stored: null }), { id: D.DESKTOP_UI_SHELL_ID, source: "url", rejected: undefined });
  assert.equal(U.resolveUiShell({ search: "", stored: D.DESKTOP_UI_SHELL_ID }).source, "storage", "the switcher's sticky choice");
});

test("the switcher offers it in every build", () => {
  registerShells();
  const out = renderToStaticMarkup(React.createElement(UiShellSwitcher, null));
  assert.match(out, /data-testid="ui-shell-switcher"/);
  assert.match(out, /<option[^>]*value="desktop"[^>]*>Desktop<\/option>/);
  assert.match(out, /<option[^>]*value="core"[^>]*>Classic<\/option>/);
});

test("its grids are already normal, and name only core panels the docks can find", () => {
  for (const grid of [D.DESKTOP_GRID, D.DESKTOP_RESULTS_GRID]) {
    assert.deepEqual(normaliseGrid(grid), grid);
    for (const id of gridPanels(grid)) assert.ok(getPanel(id), `${id} is a registered panel`);
    const groups = gridGroups(grid).map((g) => g.id);
    for (const g of Object.values(spec.layout!.docks!)) assert.ok(groups.includes(g), `dock group ${g}`);
  }
  const bottom = (g: typeof D.DESKTOP_GRID) => gridGroups(g).find((x) => x.id === D.DESKTOP_GROUPS.bottom)!;
  assert.equal(bottom(D.DESKTOP_GRID).collapsed, true, "Model: the results dock starts collapsed");
  assert.equal(bottom(D.DESKTOP_RESULTS_GRID).collapsed, undefined, "Results: open");
  assert.ok(bottom(D.DESKTOP_GRID).fixed && gridGroups(D.DESKTOP_GRID)[0].fixed, "fixed-px docks");
  for (const id of spec.layout!.open!) assert.ok(getPanel(id));
});

test("every menu item names a command core registers, or a panel or mode that exists", () => {
  registerShells();
  M.setShellModes(modes, "model");
  registerCoreCommands();
  C.registerCommand({ id: CORE_COMMAND_IDS.commandPalette, label: "Command palette", run() {} }, "core");
  try {
    const menus = desktopMenus({ modes, workspaces: ["mine"], shells: U.listUiShells(), themes: Object.keys(THEME_PRESETS) });
    assert.deepEqual(menus.map((m) => m.label), ["File", "Edit", "View", "Modes", "Build", "Window", "Help"]);
    const modeIds = new Set(modes.map((m) => m.id));
    for (const id of menuCommandIds(menus)) {
      if (id.startsWith("panel:")) {
        const p = getPanel(id.slice("panel:".length));
        assert.ok(p, `${id}: a registered panel`);
        assert.ok(Object.values(p!.placement ?? {}).some((pl) => pl && !pl.parent), `${id}: a panel core gives a command`);
      } else if (id.startsWith("mode:")) {
        assert.ok(modeIds.has(id.slice("mode:".length)), `${id}: a mode`);
      } else if (id === REOPEN_WINDOWS_COMMAND || id.startsWith("core:workspace.")) {
        // Registered while there are closed windows / saved workspaces.
      } else {
        assert.ok(C.getCommand(id), `${id}: a registered command`);
      }
    }
    // A menu with no modes has no Modes menu.
    assert.ok(!desktopMenus({ modes: [], workspaces: [], shells: [], themes: [] }).some((m) => m.label === "Modes"));
    // Keys core handles itself are shown, not bound.
    assert.equal(C.commandChord(C.getCommand(CORE_COMMAND_IDS.hideSelected)!), "Shift+H");
    assert.equal(C.commandChord(C.getCommand(CORE_COMMAND_IDS.undo)!), "Ctrl+Z");
    assert.equal(C.getCommand(CORE_COMMAND_IDS.undo)!.disabledReason?.(), "Nothing to undo");
    assert.equal(C.getCommand(CORE_COMMAND_IDS.hideSelected)!.disabledReason?.(), "Nothing selected");
  } finally {
    C.resetCommands();
    M.setShellModes([]);
  }
});

test("a menu's separators are tidied once items drop out", () => {
  const sep = { separator: true } as const;
  const out = offeredMenuItems([sep, "missing", sep, "a", sep, sep, "missing", sep], (id) => id === "a");
  assert.deepEqual(out, ["a"]);
});

test("the menu bar, the status bar and the page header render", () => {
  registerShells();
  M.setShellModes(modes, "model");
  registerCoreCommands();
  try {
    const head = html(React.createElement(DesktopHeader));
    assert.match(head, /role="menubar"/);
    for (const m of ["File", "Edit", "View", "Modes", "Window", "Help"]) assert.match(head, new RegExp(`>${m}<`));
    assert.match(head, /aria-label="Mode"/, "the mode switcher");
    assert.match(head, /Ctrl\+K/);
    const foot = html(React.createElement(StatusBar, { items: spec.chrome!.statusItems }));
    assert.match(foot, /role="status"/);
    assert.match(foot, /0 models loaded/);
    assert.match(foot, /Nothing selected/);
    assert.match(foot, /Mode: Model/);
    assert.match(foot, /Desktop UI/, "the shell's own item");
    const page = renderToStaticMarkup(React.createElement(DesktopPageHeader, { title: "Convert files", backHref: "/" }));
    assert.match(page, /href="\/"/);
    assert.match(page, /Convert files/);
  } finally {
    C.resetCommands();
    M.setShellModes([]);
  }
  assert.deepEqual(connectionLabel({ rest: false, wsOff: false, connected: false }), { text: "Not connected", ok: false });
  assert.equal(connectionLabel({ rest: true, wsOff: false, connected: false }).text, "Server");
});
