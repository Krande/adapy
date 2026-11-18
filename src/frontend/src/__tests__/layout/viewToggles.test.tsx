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
/** What a viewport without an orthographic view would answer (no built-in one is such now). */
const NO_ORTHOGRAPHIC = "This viewport has no orthographic view";

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
  const release = setActiveViewport({ kind: "three", unavailable: () => null } as never);
  try {
    useGroundGridStore.getState().setVisible(true);
    assert.equal(C.getCommand(ID.groundGrid)?.checked?.(), true);
    assert.ok(C.runCommand(ID.groundGrid));
    assert.equal(useGroundGridStore.getState().visible, false);
    assert.deepEqual(JSON.parse(window.localStorage.getItem("ada:ground-grid") ?? "null"), { state: { visible: false }, version: 1 });
    assert.equal(C.getCommand(ID.groundGrid)?.checked?.(), false);
    assert.ok(C.runCommand(ID.groundGrid));
    assert.equal(useGroundGridStore.getState().visible, true);
    assert.deepEqual(JSON.parse(window.localStorage.getItem("ada:ground-grid") ?? "null")?.state, { visible: true });
  } finally {
    release();
  }
});

test("the orthographic view is greyed, with the viewport's reason, where the viewport has none", () => {
  C.resetCommands();
  registerCoreCommands();
  const release = setActiveViewport({
    kind: "native",
    unavailable: (f: string) => (f === "orthographic" ? NO_ORTHOGRAPHIC : null),
  } as never);
  try {
    assert.equal(C.getCommand(ID.orthographic)?.disabledReason?.(), NO_ORTHOGRAPHIC);
  } finally {
    release();
  }
  useProjectionStore.getState().setOrthographic(false);
});

test("a ground grid hidden the old way (\"off\", before the store was versioned) stays hidden", async () => {
  window.localStorage.setItem("ada:ground-grid", "off");
  await useGroundGridStore.persist.rehydrate();
  assert.equal(useGroundGridStore.getState().visible, false);
  window.localStorage.removeItem("ada:ground-grid");
  useGroundGridStore.getState().setVisible(true);
});
