// The view toggles: orthographic view and the ground grid -- commands that flip their setting,
// remembered in the browser. Core puts them in its menus (Desktop UI: View, classic UI: the
// Display options); a shell may add them to its own toolbar (the docked UI does). Core's
// section strip, which the shells share, does not carry them.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const C = await import("@/layout/commands");
const { CORE_COMMAND_IDS: ID } = await import("@/layout/coreCommandIds");
const { CORE_COMMAND_ICONS } = await import("@/layout/commandIcons");
const { registerCoreCommands } = await import("@/layout/coreCommands");
const T = await import("@/layout/ModeToolbars");
const { desktopMenus, menuCommandIds } = await import("@/layout/desktop/desktopMenus");
const DisplayOptions = (await import("@/components/options/DisplayOptions")).default;
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { useGroundGridStore } = await import("@/state/groundGridStore");
const { useProjectionStore } = await import("@/state/projectionStore");
const { setActiveViewport } = await import("@/viewport/api/viewportApi");

const html = (el: React.ReactElement) => renderToStaticMarkup(React.createElement(AdaViewerProvider, null, el));

test("the ground grid and orthographic view are in core's menus, not on its shared strip", () => {
  C.resetCommands();
  registerCoreCommands();
  for (const id of [ID.groundGrid, ID.orthographic]) {
    assert.ok(C.getCommand(id), `${id} registered`);
    assert.ok(CORE_COMMAND_ICONS[id], `${id} has an icon (for a shell's toolbar)`);
  }
  const strip = html(React.createElement(T.SectionToolbar));
  assert.doesNotMatch(strip, /core:view\.orthographic|core:view\.ground-grid/);
  const ids = menuCommandIds(desktopMenus({ modes: [], workspaces: [], shells: [], themes: [] } as never));
  assert.ok(ids.includes(ID.orthographic), "Desktop UI: View > Camera");
  assert.ok(ids.includes(ID.groundGrid), "Desktop UI: View > Display");
  const options = html(React.createElement(DisplayOptions));
  assert.match(options, /Orthographic View/, "classic UI: the Display options");
  assert.match(options, /Ground Grid/);
});

test("the ground grid toggles and is remembered in the browser", () => {
  C.resetCommands();
  registerCoreCommands();
  const release = setActiveViewport({ kind: "three" } as never);
  try {
    useGroundGridStore.getState().setVisible(true);
    assert.equal(C.getCommand(ID.groundGrid)?.checked?.(), true);
    assert.ok(C.runCommand(ID.groundGrid));
    assert.equal(useGroundGridStore.getState().visible, false);
    assert.equal(window.localStorage.getItem("ada:ground-grid"), "off");
    assert.equal(C.getCommand(ID.groundGrid)?.checked?.(), false);
    assert.ok(C.runCommand(ID.groundGrid));
    assert.equal(useGroundGridStore.getState().visible, true);
    assert.equal(window.localStorage.getItem("ada:ground-grid"), null);
  } finally {
    release();
  }
});

test("the orthographic view is greyed on the native viewport, which draws in perspective only", () => {
  C.resetCommands();
  registerCoreCommands();
  const release = setActiveViewport({ kind: "native" } as never);
  try {
    assert.match(C.getCommand(ID.orthographic)?.disabledReason?.() ?? "", /perspective only/);
  } finally {
    release();
  }
  useProjectionStore.getState().setOrthographic(false);
});
