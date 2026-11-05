// A shell's chrome over core: commands (menu bar, palette and keys share them),
// workspaces and resets of its arrangements, upgrading saved arrangements when
// the shell ships new ones, HUDs over the view, and a mode's cover.

import assert from "node:assert/strict";
import { test } from "node:test";

await import("../fixtures/panelRealm");

const React = (await import("react")).default;
const { renderToStaticMarkup } = await import("react-dom/server");
const C = await import("@/layout/commands");
const S = await import("@/layout/shortcuts");
const M = await import("@/layout/shellModes");
const O = await import("@/layout/viewportOverlays");
const P = await import("@/layout/presets/classicPlace");
const { layoutStore } = await import("@/layout/layoutStore");
const { registerPanel } = await import("@/layout/registry");
const { CLASSIC_ZONES, layoutDocKey } = await import("@/layout/layout");
const { gridOf } = await import("@/layout/reducers");
const { groupOf, VIEWPORT_NODE } = await import("@/layout/splitTree");
const { MenuBar } = await import("@/layout/CommandPalette");

const body = () => Promise.resolve({ default: () => null });
registerPanel({ id: "c:p", title: "Probe", body, content: body, placement: { classic: { zone: CLASSIC_ZONES.menuColumn, order: 1 } } }, "test");

test("a command runs only while offered and runnable, and its keys are a shortcut", () => {
  let ran = 0;
  let offered = true;
  let reason: string | null = null;
  C.registerCommand({ id: "t.do", label: "Do it", group: "Test", keys: "Shift+J", run: () => ran++, when: () => offered, disabledReason: () => reason }, "t");
  try {
    assert.equal(C.runCommand("t.do"), true);
    reason = "nothing selected";
    assert.equal(C.runCommand("t.do"), false, "greyed: not run");
    reason = null;
    offered = false;
    assert.equal(C.runCommand("t.do"), false, "not offered: not run");
    assert.ok(!C.listCommands().some((c) => c.id === "t.do"), "and not listed");
    offered = true;
    assert.equal(ran, 1);
    const sc = S.listShortcuts().find((s) => s.id === "cmd:t.do");
    assert.equal(sc?.keys, "Shift+J");
    sc!.run();
    assert.equal(ran, 2, "the key runs the command");
    assert.equal(C.runCommand("nope"), false);
  } finally {
    C.resetCommands();
  }
  assert.equal(S.listShortcuts().some((s) => s.id === "cmd:t.do"), false, "its shortcut goes with it");
});

test("core lists a command per panel and per mode", () => {
  M.setShellModes([{ id: "inspect", label: "Inspect", order: 0 }]);
  try {
    const toggled: string[] = [];
    const list = C.listCommands({ toggle: (id) => toggled.push(id), isOpen: () => true });
    const probe = list.find((c) => c.id === "panel:c:p")!;
    assert.equal(probe.group, "Panels");
    probe.run();
    assert.deepEqual(toggled, ["c:p"]);
    assert.equal(probe.checked?.(), true);
    assert.equal(list.find((c) => c.id === "mode:inspect")?.checked?.(), true);
  } finally {
    M.setShellModes([]);
  }
});

test("the palette's match ranks a prefix first, then a substring, then letters in order", () => {
  assert.equal(C.matchCommand("", "Anything"), 0);
  assert.equal(C.matchCommand("sce", "Scene info"), 0);
  assert.ok(C.matchCommand("info", "Scene info")! > 0);
  assert.equal(C.matchCommand("si", "Scene info"), 3);
  assert.equal(C.matchCommand("xyz", "Scene info"), null);
});

test("the menu bar draws a shell's menus over the commands, a missing one left out", () => {
  C.registerCommand({ id: "t.open", label: "Open…", keys: "Shift+O", run() {} }, "t");
  try {
    const html = renderToStaticMarkup(React.createElement(MenuBar, { menus: [{ label: "File", items: ["t.open", { separator: true }, "missing"] }] }));
    assert.match(html, /role="menubar"/);
    assert.match(html, />File</);
    assert.doesNotMatch(html, /Open…/, "closed menus draw no items");
  } finally {
    C.resetCommands();
  }
});

test("workspaces save and load every mode's arrangement; a reset starts the active one over", () => {
  M.setShellModes([{ id: "a", label: "A", order: 0 }, { id: "b", label: "B", order: 1 }], "a");
  P.setClassicShell({ id: "ws" }, ["a", "b"]);
  try {
    P.dockAt("c:p", { node: VIEWPORT_NODE, edge: "left" });
    P.saveWorkspace("mine");
    assert.deepEqual(P.listWorkspaces(), ["mine"]);
    P.resetArrangement();
    assert.equal(groupOf(gridOf(P.classicDoc()), "c:p"), null, "reset: back to the starting arrangement");
    assert.equal(P.loadWorkspace("mine"), true);
    assert.ok(groupOf(gridOf(P.classicDoc()), "c:p"), "loaded back");
    assert.equal(P.loadWorkspace("other"), false);
    P.deleteWorkspace("mine");
    assert.deepEqual(P.listWorkspaces(), []);
  } finally {
    M.setShellModes([]);
    P.setClassicShell(null);
    layoutStore.setState({ prefs: { v: 1, docs: {} } });
  }
});

test("a saved arrangement of an older shell version is migrated, or starts over without a migrate", () => {
  const key = layoutDocKey("classic", "vs");
  P.setClassicShell({ id: "vs", version: 1 });
  P.dockAt("c:p", { node: VIEWPORT_NODE, edge: "left" });
  assert.equal(layoutStore.getState().prefs.docs[key]?.shellVersion, 1, "saved under the shell's version");
  try {
    P.setClassicShell({ id: "vs", version: 2 });
    P.upgradeShellDocs();
    assert.equal(layoutStore.getState().prefs.docs[key], undefined, "no migrate: starts over");
    P.setClassicShell({ id: "vs", version: 2 });
    P.dockAt("c:p", { node: VIEWPORT_NODE, edge: "left" });
    let seen: number | undefined = -1;
    P.setClassicShell({ id: "vs", version: 3, migrate: (doc, from) => ((seen = from), doc) });
    P.upgradeShellDocs();
    assert.equal(seen, 2);
    assert.equal(layoutStore.getState().prefs.docs[key]?.shellVersion, 3);
    assert.ok(groupOf(gridOf(P.classicDoc()), "c:p"), "kept by the migrate");
  } finally {
    P.setClassicShell(null);
    layoutStore.setState({ prefs: { v: 1, docs: {} } });
  }
});

test("HUDs register at anchors, in order", () => {
  O.registerViewportOverlay({ id: "b", anchor: "top-right", order: 2, render: () => null }, "t");
  O.registerViewportOverlay({ id: "a", anchor: "top-right", order: 1, render: () => null }, "t");
  O.registerViewportOverlay({ id: "c", anchor: "bottom-left", render: () => null }, "t");
  try {
    assert.deepEqual(O.overlaysAt("top-right").map((o) => o.id), ["a", "b"]);
    O.unregisterViewportOverlay("a");
    assert.deepEqual(O.overlaysAt("top-right").map((o) => o.id), ["b"]);
  } finally {
    O.resetViewportOverlays();
  }
});

test("a mode's cover and a shell's overlay panels", () => {
  M.setShellModes([{ id: "convert", label: "Convert", order: 0 }, { id: "view", label: "View", order: 1 }], "convert");
  P.setClassicShell({ id: "cv", modes: { convert: { cover: "c:p" } }, overlays: { pluginPanels: "bottom-left" } }, ["convert", "view"]);
  try {
    assert.equal(P.classicCover(), "c:p");
    M.setShellMode("view");
    assert.equal(P.classicCover(), null);
    assert.deepEqual(P.classicOverlays(), { pluginPanels: "bottom-left" });
    registerPanel({ id: "plug:hud", title: "HUD", body, content: body, placement: { classic: { zone: CLASSIC_ZONES.menuColumn, order: 1 }, docked: { zone: "overlay" } } }, "plug");
    const { getPanel } = await_registry;
    assert.equal(P.isOverlayPanel(getPanel("plug:hud")!), true);
    assert.ok(P.placedElsewhere(P.classicDoc(), true, false).includes("plug:hud"), "its region host leaves it out");
  } finally {
    M.setShellModes([]);
    P.setClassicShell(null);
    layoutStore.setState({ prefs: { v: 1, docs: {} } });
  }
});

const await_registry = await import("@/layout/registry");
