// What a composed shell can configure beyond the arrangement: the mode it opens
// in, hiding core panels (all the time or per mode), docking the sections the
// classic UI draws inside another panel, a palette for one panel's body, the
// wrapper that gives bodies outside panels the shell's palette, and the
// deformation default of a plugin's mode.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { CORE_PANEL_IDS, registerCorePanels } = await import("@/layout/corePanels");
const { getPanel, listPanels, isPanelAvailable, setPanelOverrides, registerPanel } = await import("@/layout/registry");
const { emptyLayoutDoc, dockInGridDoc } = await import("@/layout/reducers");
const { VIEWPORT_NODE } = await import("@/layout/splitTree");
const P = await import("@/layout/presets/classicPlace");
const M = await import("@/layout/shellModes");
const { initialShellMode, shellModes } = await import("@/layout/ComposedLayout");
const { bodyPaletteCss, panelBodyAttrsFor, panelBodyAttrs } = await import("@/components/ui/panelChrome");
const { PanelBodyPalette } = await import("@/components/ui/PanelBodyPalette");
const { registerPlugin, resetRegistry } = await import("@/plugins/registry");

registerCorePanels();

test("a shell opens in ?mode=, else the stored mode, else its default mode, else the first", () => {
  const modes = [{ id: "a" }, { id: "b" }, { id: "c" }];
  assert.equal(initialShellMode(modes, "c", "b", "b"), "c");
  assert.equal(initialShellMode(modes, null, "b", "c"), "b");
  assert.equal(initialShellMode(modes, null, null, "c"), "c");
  assert.equal(initialShellMode(modes, "zz", "yy", "xx"), null, "ids the shell does not have are passed over");
  assert.equal(initialShellMode(modes, null, "gone", "c"), "c");
  M.setShellModes(modes.map((m, i) => ({ ...m, label: m.id, order: i })), initialShellMode(modes, null, null, undefined));
  assert.equal(M.activeShellMode(), "a", "no opinion: the first");
  M.setShellModes([]);
});

test("a core panel the classic UI draws inside another can be docked in a composed shell only", () => {
  const table = getPanel(CORE_PANEL_IDS.feaTable)!;
  assert.equal(P.canDock(table), false, "core's own UI keeps it in the Simulation panel");
  assert.deepEqual(P.placedElsewhere(dockedDoc(), true, false), []);
  P.setClassicShell({ id: "sections-shell" });
  try {
    assert.equal(P.canDock(table), true);
    assert.ok(P.placedElsewhere(dockedDoc(), true, false).includes(CORE_PANEL_IDS.feaTable), "it leaves its host once the grid places it");
    assert.ok(!P.placedElsewhere(emptyLayoutDoc("classic", []), true, false).includes(CORE_PANEL_IDS.feaTable), "at home it stays in its host");
  } finally {
    P.setClassicShell(null);
  }
  assert.equal(P.canDock(table), false);
});

function dockedDoc() {
  return dockInGridDoc(emptyLayoutDoc("classic", []), CORE_PANEL_IDS.feaTable, { node: VIEWPORT_NODE, edge: "right" });
}

test("a shell hides a core panel entirely, or in some modes", () => {
  const id = CORE_PANEL_IDS.componentControls;
  assert.ok(listPanels().some((p) => p.id === id));
  setPanelOverrides({ [id]: { available: false } });
  try {
    assert.ok(!listPanels().some((p) => p.id === id), "left out of the registry's lists");
    assert.ok(!listPanels({}).some((p) => p.id === id));
    assert.equal(isPanelAvailable(getPanel(id)!, {}), false);
  } finally {
    setPanelOverrides(null);
  }
  assert.ok(listPanels().some((p) => p.id === id));
});

test("a panel hidden per mode comes back in the other modes", () => {
  registerPanel({ id: "t:visible", title: "Visible", body: () => Promise.resolve({ default: () => null }) }, "test");
  setPanelOverrides({ "t:visible": { available: (mode) => mode !== "hide-here" } });
  M.setShellModes([{ id: "hide-here", label: "H", order: 0 }, { id: "show-here", label: "S", order: 1 }], "hide-here");
  try {
    assert.ok(!listPanels().some((p) => p.id === "t:visible"));
    assert.equal(isPanelAvailable(getPanel("t:visible")!, {}), false);
    M.setShellMode("show-here");
    assert.ok(listPanels().some((p) => p.id === "t:visible"));
    assert.equal(isPanelAvailable(getPanel("t:visible")!, {}), true);
  } finally {
    M.setShellModes([]);
    setPanelOverrides(null);
  }
});

test("an override can only take a panel away: its own availability still holds", () => {
  registerPanel(
    { id: "t:never", title: "Never", body: () => Promise.resolve({ default: () => null }), available: () => false },
    "test",
  );
  setPanelOverrides({ "t:never": { available: true } });
  try {
    assert.equal(isPanelAvailable(getPanel("t:never")!, {}), false);
  } finally {
    setPanelOverrides(null);
  }
});

test("a panel's body palette is scoped to that panel's body and follows the shell-wide one", () => {
  assert.equal(
    bodyPaletteCss({ "--color-gray-800": "#111" }, { "core:scene": { "--color-blue-600": "#0a0" }, "x:y": undefined }),
    '[data-ada-panel-body]{--color-gray-800:#111;}[data-ada-panel-body][data-ada-panel-id="core:scene"]{--color-blue-600:#0a0;}',
  );
  assert.equal(bodyPaletteCss(null, { a: { color: "red" } }), "", "custom properties only");
  assert.ok(!bodyPaletteCss(null, { 'a"]{x:y}': { "--c": "red" } }).includes('"]{x'), "an id cannot close the selector");
  assert.deepEqual({ ...panelBodyAttrsFor("core:scene") }, { "data-ada-panel-body": "", "data-ada-panel-id": "core:scene" });
  assert.equal(panelBodyAttrsFor(undefined), panelBodyAttrs);
});

test("PanelBodyPalette marks a body outside a panel without drawing a box", () => {
  assert.equal(
    renderToStaticMarkup(React.createElement(PanelBodyPalette, null, React.createElement("i", null, "x"))),
    '<div class="contents" data-ada-panel-body=""><i>x</i></div>',
  );
  const html = renderToStaticMarkup(React.createElement(PanelBodyPalette, { panelId: "core:storage", className: "p-2" }, "x"));
  assert.match(html, /data-ada-panel-body=""/);
  assert.match(html, /data-ada-panel-id="core:storage"/);
  assert.match(html, /class="p-2"/);
});

test("a plugin's mode holds the FEA model undeformed unless it opts in", () => {
  resetRegistry();
  registerPlugin({
    id: "mode-plugin",
    version: "1.0.0",
    coreApiRange: ">=1.0 <2.0",
    modes: [
      { id: "plain", label: "Plain" },
      { id: "warps", label: "Warps", showsDeformation: true },
    ],
  });
  try {
    const modes = shellModes({
      id: "s",
      label: "S",
      layout: { modes: [{ id: "own", label: "Own" }] },
    });
    const by = Object.fromEntries(modes.map((m) => [m.id, m.showsDeformation]));
    assert.equal(by.plain, false);
    assert.equal(by.warps, true);
    assert.equal(by.own, undefined, "the shell's own modes keep the default (shown)");
  } finally {
    resetRegistry();
  }
});
