// "Customisable panels" is the user's per UI shell, and a composed shell (the
// Desktop UI) requires it: always on there, the Options toggle disabled, and the
// user's own setting for core's UI kept untouched across a switch.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const { layoutStore, migrateLayoutPrefs, LAYOUT_PREFS_VERSION } = await import("@/layout/layoutStore");
const { customisablePanelsOn } = await import("@/layout/layout");
const K = await import("@/layout/customisable");
const { installShell } = await import("@/layout/ComposedLayout");
const { setClassicShell } = await import("@/layout/presets/classicPlace");
const { desktopUiShellSpec } = await import("@/layout/desktop/desktopShell");
const { default: LayoutOptions } = await import("@/components/options/LayoutOptions");
const U = await import("@/plugins/uiShells");

const reset = () => layoutStore.setState({ prefs: { v: LAYOUT_PREFS_VERSION, docs: {} } });

test("the one setting of before becomes core's own UI's; other shells start from their default", () => {
  const p = migrateLayoutPrefs({ v: LAYOUT_PREFS_VERSION, customisablePanels: false, docs: {} });
  assert.deepEqual(p.customisablePanelsByShell, { core: false });
  assert.equal("customisablePanels" in p, false, "moved, not kept twice");
  assert.equal(customisablePanelsOn(p), false);
  assert.equal(customisablePanelsOn(p, "other"), true, "another shell: on by default");
  const kept = migrateLayoutPrefs({ v: LAYOUT_PREFS_VERSION, customisablePanels: true, customisablePanelsByShell: { core: false, x: false, bad: "no" }, docs: {} });
  assert.deepEqual(kept.customisablePanelsByShell, { core: false, x: false }, "a per-shell value wins; junk is dropped");
  assert.equal(customisablePanelsOn({ customisablePanels: false }), false, "an unmigrated value still reads as core's");
});

test("the setting is kept per shell", () => {
  reset();
  layoutStore.getState().setCustomisablePanels(false);
  layoutStore.getState().setCustomisablePanels(false, "alt");
  layoutStore.getState().setCustomisablePanels(true, "alt");
  assert.deepEqual(layoutStore.getState().prefs.customisablePanelsByShell, { core: false, alt: true });
  reset();
});

test("a composed shell requires customisable panels; Classic(off) -> Desktop -> Classic keeps Classic's own setting", () => {
  reset();
  try {
    installShell(null);
    K.setCustomisablePanels(false);
    assert.equal(K.customisablePanels(), false, "core's UI: the user turned it off");
    assert.equal(K.customisablePanelsRequired(), false);

    installShell(desktopUiShellSpec());
    assert.equal(K.customisablePanelsRequired(), true);
    assert.equal(K.customisablePanels(), true, "always on in the Desktop UI");
    K.setCustomisablePanels(false);
    assert.equal(K.customisablePanels(), true, "and not the user's to turn off there");

    installShell(null);
    assert.equal(K.customisablePanels(), false, "back in core's UI: its own setting, still off");
    assert.deepEqual(layoutStore.getState().prefs.customisablePanelsByShell, { core: false });

    // A composed shell may leave it to the user, under its own id.
    setClassicShell({ id: "loose", customisablePanels: "optional" });
    assert.equal(K.customisablePanels(), true, "its own default: on");
    K.setCustomisablePanels(false);
    assert.equal(K.customisablePanels(), false);
    setClassicShell(null);
    assert.equal(K.customisablePanels(), false);
    assert.deepEqual(layoutStore.getState().prefs.customisablePanelsByShell, { core: false, loose: false });
  } finally {
    installShell(null);
    reset();
  }
});

test("Options shows the toggle on and disabled in a shell that requires it, with the reason", () => {
  reset();
  U.resetUiShells();
  U.registerUiShell(desktopUiShellSpec(), "core");
  try {
    installShell(null);
    K.setCustomisablePanels(false);
    // (Server rendering reads the store's initial state, so only what does not depend on it is checked here.)
    let html = renderToStaticMarkup(React.createElement(LayoutOptions));
    assert.doesNotMatch(html, /disabled/, "core's UI: the user's to change");

    localStorage.setItem(U.UI_SHELL_STORAGE_KEY, "desktop");
    U.setActiveUiShell("desktop", { reload: false });
    installShell(U.getUiShell("desktop")!);
    html = renderToStaticMarkup(React.createElement(LayoutOptions));
    assert.match(html, /checked=""/);
    assert.match(html, /disabled=""/);
    assert.match(html, /Always on in the Desktop UI/);
  } finally {
    installShell(null);
    localStorage.removeItem(U.UI_SHELL_STORAGE_KEY);
    U.resetUiShells();
    reset();
  }
});
