// The view strip's toggles: orthographic view and the ground grid -- commands that flip their
// setting, remembered in the browser, and buttons on the strip every mode shows.

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
const { AdaViewerProvider } = await import("@/state/AdaViewerContext");
const { useGroundGridStore } = await import("@/state/groundGridStore");
const { useProjectionStore } = await import("@/state/projectionStore");
const { setActiveViewport } = await import("@/viewport/api/viewportApi");

const html = (el: React.ReactElement) => renderToStaticMarkup(React.createElement(AdaViewerProvider, null, el));

test("the ground grid and orthographic view are commands on the strip every mode shows", () => {
  C.resetCommands();
  registerCoreCommands();
  for (const id of [ID.groundGrid, ID.orthographic]) {
    assert.ok(C.getCommand(id), `${id} registered`);
    assert.ok(CORE_COMMAND_ICONS[id], `${id} has an icon`);
  }
  const strip = html(React.createElement(T.SectionToolbar));
  assert.match(strip, /data-command="core:view\.orthographic"/);
  assert.match(strip, /data-command="core:view\.ground-grid"/);
  assert.ok(strip.indexOf("core:view.ground-grid") < strip.indexOf("core:section.x"), "before the section planes");
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
