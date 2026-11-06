// A plugin's panel slots land in the panel registry when the plugin registers,
// with titles, placements and bindings derived from the slot -- and the slot
// itself, which the classic region hosts read, is unchanged.

import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";

import { placedElsewhereVersion, setPlacedElsewhere } from "@/layout/placedElsewhere";
import { dockZoneFor, setPluginPanelVisibility } from "@/layout/pluginPanels";
import { getPanel, listPanels, listToolbarPanels, resetPanelRegistry } from "@/layout/registry";
import { hide, isPanelOpen, reveal, toggle } from "@/layout/reveal";
import { usePluginUiStore } from "@/plugins/pluginUiStore";
import {
  disablePlugin,
  getPanelsForRegion,
  getRegisteredPlugins,
  getSimulationTabs,
  registerPlugin,
  resetRegistry,
  setPluginContextFactory,
  type PanelSlot,
} from "@/plugins/registry";

const Icon = () => null;
const render = () => null;

beforeEach(() => {
  resetRegistry();
  resetPanelRegistry();
  usePluginUiStore.setState({ visible: {} });
  setPluginPanelVisibility(usePluginUiStore);
});

afterEach(() => setPluginPanelVisibility(null));

test("every accepted slot is registered under its namespaced id, owned by the plugin", () => {
  registerPlugin({
    id: "demo",
    panels: [
      { id: "inline", region: "top-panel", render },
      { id: "demo:already", region: "top-panel", render },
    ],
    topBarButtons: [{ id: "btn", region: "top-panel", render, topBarButton: { icon: Icon, label: "Demo" } }],
  });
  assert.deepEqual(
    listPanels().map((p) => [p.id, p.owner]),
    [
      ["demo:inline", "demo"],
      ["demo:already", "demo"],
      ["demo:btn", "demo"],
    ],
  );
  // The slots the region hosts read are untouched.
  assert.deepEqual(
    getRegisteredPlugins()[0].panels.map((p) => p.id),
    ["demo:inline", "demo:already", "demo:btn"],
  );
  assert.equal(getPanel("demo:inline")?.title, "inline");
  assert.equal(getPanel("demo:inline")?.frame, "own");
});

test("a skipped plugin registers no panels", () => {
  registerPlugin({ id: "future", coreApiRange: ">=2.0", panels: [{ id: "p", region: "top-panel", render }] });
  registerPlugin({ id: "", panels: [{ id: "p", region: "top-panel", render }] });
  assert.deepEqual(listPanels(), []);
});

test("asTab: a tab inside the Simulation panel, titled by the tab label", () => {
  const slot: PanelSlot = {
    id: "results",
    region: "fem-sidebar",
    render,
    asTab: { label: "My results", order: 3 },
  };
  registerPlugin({ id: "res", panels: [slot, { id: "inline", region: "fem-sidebar", render, order: 7 }] });
  const tab = getPanel("res:results");
  assert.equal(tab?.title, "My results");
  assert.deepEqual(tab?.placement?.classic, {
    zone: "classic.menu-column",
    parent: "core:simulation",
    as: "tab",
    order: 3,
  });
  assert.equal(tab?.binding, undefined);
  assert.equal(tab?.toolbar, undefined);
  assert.deepEqual(getPanel("res:inline")?.placement?.classic, {
    zone: "classic.menu-column",
    parent: "core:simulation",
    as: "inline",
    order: 7,
  });
});

test("the other regions map to their classic hosts", () => {
  registerPlugin({
    id: "r",
    panels: [
      { id: "top", region: "top-panel", render, order: 4 },
      { id: "adm", region: "admin", render },
      { id: "sci", region: "scene-info", render },
      { id: "sto", region: "storage-detail", render },
    ],
  });
  assert.deepEqual(getPanel("r:top")?.placement?.classic, { zone: "classic.menu-column", order: 1004 });
  assert.deepEqual(getPanel("r:adm")?.placement?.classic?.parent, "core:admin");
  assert.deepEqual(getPanel("r:sci")?.placement?.classic?.parent, "core:scene");
  assert.deepEqual(getPanel("r:sto")?.placement?.classic?.parent, "core:storage");
});

test("topBarButton: titled and iconed by the button, a toolbar entry, bound to the plugin UI store", () => {
  registerPlugin({
    id: "tb",
    topBarButtons: [
      { id: "b2", region: "top-panel", render, order: 2, topBarButton: { icon: Icon, label: "Two" } },
      { id: "b1", region: "top-panel", render, order: 1, topBarButton: { icon: Icon, label: "One" } },
    ],
  });
  const p = getPanel("tb:b1");
  assert.equal(p?.title, "One");
  assert.equal(p?.icon, Icon);
  assert.deepEqual(listToolbarPanels().map((x) => x.id), ["tb:b1", "tb:b2"]);

  // The binding is the key PluginTopBarButtons toggles and PluginPanelRegion reads.
  assert.equal(isPanelOpen("tb:b1"), false);
  reveal("tb:b1");
  assert.deepEqual(usePluginUiStore.getState().visible, { "tb:b1": true });
  usePluginUiStore.getState().toggle("tb:b1"); // what the top-bar button does
  assert.equal(isPanelOpen("tb:b1"), false);
  toggle("tb:b1");
  assert.equal(usePluginUiStore.getState().isVisible("tb:b1"), true);
  hide("tb:b1");
  assert.equal(usePluginUiStore.getState().isVisible("tb:b1"), false);
});

test("a top-bar button outside top-panel is not a toolbar entry (core shows none for it)", () => {
  registerPlugin({
    id: "x",
    panels: [{ id: "f", region: "fem-sidebar", render, topBarButton: { icon: Icon, label: "F" } }],
  });
  assert.equal(getPanel("x:f")?.toolbar, undefined);
});

test("dock: the docked preset's zone, unknown docks fall back to right", () => {
  registerPlugin({
    id: "d",
    panels: [
      { id: "l", region: "top-panel", render, dock: "left" },
      { id: "aux", region: "top-panel", render, dock: "right-aux2" },
      { id: "odd", region: "top-panel", render, dock: "sideways" as never },
      { id: "none", region: "top-panel", render, modes: ["inspect"] },
    ],
  });
  assert.equal(getPanel("d:l")?.placement?.docked?.zone, "left");
  assert.equal(getPanel("d:aux")?.placement?.docked?.zone, "right-aux2");
  assert.equal(getPanel("d:odd")?.placement?.docked?.zone, "right");
  assert.equal(getPanel("d:none")?.placement?.docked, undefined);
  assert.deepEqual(getPanel("d:none")?.modes, ["inspect"]);
  assert.equal(dockZoneFor("bottom"), "bottom");
});

test("a disabled plugin's panels are unavailable", () => {
  registerPlugin({ id: "flaky", panels: [{ id: "p", region: "top-panel", render }] });
  registerPlugin({ id: "fine", panels: [{ id: "p", region: "top-panel", render }] });
  assert.deepEqual(listPanels({}).map((p) => p.id), ["flaky:p", "fine:p"]);
  disablePlugin("flaky", "test");
  assert.deepEqual(listPanels({}).map((p) => p.id), ["fine:p"]);
  // Still registered: availability, not removal.
  assert.ok(getPanel("flaky:p"));
});

test("a dormant plugin's panels are unavailable once the bootstrap gives a context to ask with", () => {
  let awake = false;
  registerPlugin({ id: "sleepy", activationPredicate: () => awake, panels: [{ id: "p", region: "top-panel", render }] });
  assert.deepEqual(listPanels({}).map((p) => p.id), ["sleepy:p"], "no context: only disabling counts");
  setPluginContextFactory((pluginId) => ({ pluginId }) as never);
  try {
    assert.deepEqual(listPanels({}).map((p) => p.id), []);
    awake = true;
    assert.deepEqual(listPanels({}).map((p) => p.id), ["sleepy:p"]);
  } finally {
    setPluginContextFactory(null);
  }
});

test("resetRegistry drops the plugins' panels too, so a re-registration is not a duplicate", () => {
  registerPlugin({ id: "again", panels: [{ id: "p", region: "top-panel", render, order: 1 }] });
  resetRegistry();
  assert.deepEqual(listPanels(), []);
  registerPlugin({ id: "again", panels: [{ id: "p", region: "top-panel", render, order: 2 }] });
  assert.equal(getPanel("again:p")?.placement?.classic?.order, 1002);
});

test("the body loader is lazy: registering does not load it", () => {
  registerPlugin({ id: "lazy", panels: [{ id: "p", region: "top-panel", render }] });
  assert.equal(typeof getPanel("lazy:p")?.body, "function");
});

test("a panel that stands on its own has content a layout can place; a section without a dock has none", () => {
  registerPlugin({
    id: "c",
    panels: [
      { id: "col", region: "top-panel", render },
      { id: "sec", region: "scene-info", render },
      { id: "secDock", region: "scene-info", render, dock: "right" },
      { id: "tab", region: "fem-sidebar", render, asTab: { label: "T" }, dock: "bottom" },
    ],
  });
  assert.equal(typeof getPanel("c:col")?.content, "function");
  assert.equal(getPanel("c:sec")?.content, undefined);
  assert.equal(typeof getPanel("c:secDock")?.content, "function");
  assert.equal(typeof getPanel("c:tab")?.content, "function");
});

test("a panel the layout places elsewhere leaves its region host", () => {
  registerPlugin({
    id: "e",
    panels: [
      { id: "a", region: "top-panel", render },
      { id: "b", region: "top-panel", render },
      { id: "t", region: "fem-sidebar", render, asTab: { label: "T" } },
    ],
  });
  const ctx = {} as never;
  try {
    setPlacedElsewhere(["e:a", "e:t"]);
    assert.deepEqual(getPanelsForRegion("top-panel", ctx).map((p) => p.panel.id), ["e:b"]);
    // The top-bar buttons still list it: they open and close it wherever it is.
    assert.deepEqual(getPanelsForRegion("top-panel", ctx, { includePlacedElsewhere: true }).map((p) => p.panel.id), ["e:a", "e:b"]);
    assert.deepEqual(getSimulationTabs(ctx), []);
    const before = placedElsewhereVersion();
    setPlacedElsewhere(["e:t", "e:a"]);
    assert.equal(placedElsewhereVersion(), before, "the same set is no change");
  } finally {
    setPlacedElsewhere([]);
  }
  assert.deepEqual(getPanelsForRegion("top-panel", ctx).map((p) => p.panel.id), ["e:a", "e:b"]);
});
