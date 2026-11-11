// Core's panel catalogue against the classic UI it describes.
//
// Two properties, both guarding the same failure -- a panel the classic UI has
// but the catalogue does not, which no other host can then find:
//
//   1. The classic top bar's toggles are registered panels with a toolbar
//      entry, in the order the classic UI has always drawn them, and the menu
//      column's panels are registered in the order it stacks them.
//   2. Each binding writes exactly the flag the classic toggle wrote (its
//      store's own setter), and no other: `toggle(id)` from a given state ends
//      in the same state as that setter.

import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import type { PanelEnv } from "@/layout/panel";

// The store modules read `window` at import time; give them a minimal realm,
// as the multi-instance provider test does.
const g = globalThis as Record<string, unknown>;
const cells = new Map<string, string>();
const storage = {
  getItem: (k: string) => (cells.has(k) ? cells.get(k)! : null),
  setItem: (k: string, v: string) => void cells.set(k, String(v)),
  removeItem: (k: string) => void cells.delete(k),
  clear: () => cells.clear(),
  key: () => null,
  length: 0,
};
g.window ??= {
  location: new URL("http://localhost/"),
  localStorage: storage,
  sessionStorage: storage,
  devicePixelRatio: 1,
  addEventListener() {},
  removeEventListener() {},
  matchMedia: () => ({ matches: false, addEventListener() {}, removeEventListener() {} }),
};
g.localStorage ??= storage;
g.sessionStorage ??= storage;
g.document ??= {
  documentElement: { style: { setProperty() {} }, classList: { add() {}, remove() {} } },
  createElement: () => ({ style: {}, getContext: () => null }),
  addEventListener() {},
  removeEventListener() {},
};

const { CORE_PANEL_IDS, corePanelSpecs, registerCorePanels } = await import("@/layout/corePanels");
const { getPanel, listPanels, listToolbarPanels, resetPanelRegistry } = await import("@/layout/registry");
const { placementFor } = await import("@/layout/panel");
const { PLUGIN_TOOLBAR_ORDER } = await import("@/layout/pluginPanels");
const panelActions = await import("@/layout/panelActions");
const { toggle, reveal, hide } = await import("@/layout/reveal");
const { useOptionsStore } = await import("@/state/optionsStore");
const { useTreeViewStore } = await import("@/state/treeViewStore");
const { useServerInfoStore } = await import("@/state/serverInfoStore");
const { useSearchWindowStore } = await import("@/state/searchWindowStore");
const { useObjectInfoStore } = await import("@/state/objectInfoStore");
const { useSceneInfoStore } = await import("@/state/sceneInfoStore");
const { useAnimationStore } = await import("@/state/animationStore");
const { useComponentControlsStore } = await import("@/state/componentControlsStore");
const { useCellBuilderStore } = await import("@/state/cellBuilderStore");
const { useEquipmentCatalogStore } = await import("@/state/equipmentCatalogStore");
const { useWebsocketStatusStore } = await import("@/state/websocketStatusStore");
const { useViewerPanelStore } = await import("@/state/viewerPanelStore");
const { useTableNavStore } = await import("@/state/tableNavStore");
const { useSimulationStore } = await import("@/state/simulationStore");
const { useLayersPanelStore } = await import("@/state/layersPanelStore");

/** Every visibility flag the classic UI keeps, by name. */
function flags(): Record<string, unknown> {
  const cb = useEquipmentCatalogStore.getState();
  return {
    isOptionsVisible: useOptionsStore.getState().isOptionsVisible,
    isTreeCollapsed: useTreeViewStore.getState().isTreeCollapsed,
    showServerInfoBox: useServerInfoStore.getState().showServerInfoBox,
    searchWindowOpen: useSearchWindowStore.getState().open,
    show_info_box: useObjectInfoStore.getState().show_info_box,
    show_scene_info_box: useSceneInfoStore.getState().show_scene_info_box,
    isControlsVisible: useAnimationStore.getState().isControlsVisible,
    componentControlsVisible: useComponentControlsStore.getState().isVisible,
    cellBuilderPanelVisible: useCellBuilderStore.getState().panelVisible,
    equipmentPanelOpen: cb.equipmentPanelOpen,
    systemPanelOpen: cb.systemPanelOpen,
    showWebsocketInfoBox: useWebsocketStatusStore.getState().showInfoBox,
    viewerPanelOpen: useViewerPanelStore.getState().open,
    tablePanelOpen: useTableNavStore.getState().isPanelOpen,
    simulationsPanelOpen: useSimulationStore.getState().panelOpen,
    layersPanelOpen: useLayersPanelStore.getState().panelOpen,
  };
}

function resetFlags(): void {
  useOptionsStore.setState({ isOptionsVisible: false });
  useTreeViewStore.setState({ isTreeCollapsed: true });
  useServerInfoStore.setState({ showServerInfoBox: false });
  useSearchWindowStore.setState({ open: false });
  useObjectInfoStore.setState({ show_info_box: false });
  useSceneInfoStore.setState({ show_scene_info_box: false });
  useAnimationStore.setState({ isControlsVisible: false });
  useComponentControlsStore.setState({ isVisible: false });
  useCellBuilderStore.setState({ panelVisible: false });
  useEquipmentCatalogStore.setState({ equipmentPanelOpen: false, systemPanelOpen: false });
  useWebsocketStatusStore.setState({ showInfoBox: false });
  useViewerPanelStore.setState({ open: null, shown: [], exclusive: true, adminTab: null });
  useTableNavStore.setState({ isPanelOpen: false });
  useSimulationStore.setState({ panelOpen: false });
  useLayersPanelStore.setState({ panelOpen: false });
}

/**
 * The classic top bar, toggle by toggle in its order: the flag its pressed
 * state reads, the panel it toggles, and the store setter the classic button
 * called.
 */
const MENU_TOGGLES: Array<{ pressed: string; id: string; flag: string; onClick: () => void }> = [
  {
    pressed: "isOptionsVisible",
    id: CORE_PANEL_IDS.options,
    flag: "isOptionsVisible",
    onClick: () => {
      const { isOptionsVisible, setIsOptionsVisible } = useOptionsStore.getState();
      setIsOptionsVisible(!isOptionsVisible);
    },
  },
  {
    pressed: "!isTreeCollapsed",
    id: CORE_PANEL_IDS.tree,
    flag: "isTreeCollapsed",
    onClick: () => {
      const { isTreeCollapsed, setIsTreeCollapsed } = useTreeViewStore.getState();
      setIsTreeCollapsed(!isTreeCollapsed);
    },
  },
  {
    pressed: "showServerInfoBox",
    id: CORE_PANEL_IDS.storage,
    flag: "showServerInfoBox",
    onClick: () => {
      const { showServerInfoBox, setShowServerInfoBox } = useServerInfoStore.getState();
      setShowServerInfoBox(!showServerInfoBox);
    },
  },
  {
    pressed: "searchWindowOpen",
    id: CORE_PANEL_IDS.attributeSearch,
    flag: "searchWindowOpen",
    onClick: () => useSearchWindowStore.getState().toggle(),
  },
  {
    pressed: "show_info_box",
    id: CORE_PANEL_IDS.objectInfo,
    flag: "show_info_box",
    onClick: () => useObjectInfoStore.getState().toggle(),
  },
  {
    pressed: "show_scene_info_box",
    id: CORE_PANEL_IDS.scene,
    flag: "show_scene_info_box",
    onClick: () => useSceneInfoStore.getState().toggle(),
  },
  {
    pressed: "isControlsVisible",
    id: CORE_PANEL_IDS.simulation,
    flag: "isControlsVisible",
    onClick: () => {
      const { isControlsVisible, setIsControlsVisible } = useAnimationStore.getState();
      setIsControlsVisible(!isControlsVisible);
    },
  },
  {
    pressed: "componentControlsVisible",
    id: CORE_PANEL_IDS.componentControls,
    flag: "componentControlsVisible",
    onClick: () => useComponentControlsStore.getState().toggleVisible(),
  },
  {
    pressed: "cellBuilderPanelVisible",
    id: CORE_PANEL_IDS.cellbuilder,
    flag: "cellBuilderPanelVisible",
    onClick: () =>
      useCellBuilderStore.getState().setPanelVisible(!useCellBuilderStore.getState().panelVisible),
  },
  {
    pressed: "simulationsPanelOpen",
    id: CORE_PANEL_IDS.simulations,
    flag: "simulationsPanelOpen",
    onClick: () => useSimulationStore.getState().setPanelOpen(!useSimulationStore.getState().panelOpen),
  },
  {
    pressed: "layersPanelOpen",
    id: CORE_PANEL_IDS.layers,
    flag: "layersPanelOpen",
    onClick: () => useLayersPanelStore.getState().setPanelOpen(!useLayersPanelStore.getState().panelOpen),
  },
  {
    pressed: "showWebsocketInfoBox",
    id: CORE_PANEL_IDS.wsStatus,
    flag: "showWebsocketInfoBox",
    // The status menu's own click handler toggles the box.
    onClick: () => useWebsocketStatusStore.getState().toggleShowInfoBox(),
  },
];

/** The flags the classic UI gates or renders panels on that have no top-bar button. */
const OTHER_BINDINGS: Array<{ id: string; flag: string; open: unknown; closed: unknown }> = [
  { id: CORE_PANEL_IDS.serverInfo, flag: "showServerInfoBox", open: true, closed: false },
  { id: CORE_PANEL_IDS.feaTable, flag: "tablePanelOpen", open: true, closed: false },
  { id: CORE_PANEL_IDS.equipmentAdmin, flag: "equipmentPanelOpen", open: true, closed: false },
  { id: CORE_PANEL_IDS.systemAdmin, flag: "systemPanelOpen", open: true, closed: false },
  { id: CORE_PANEL_IDS.admin, flag: "viewerPanelOpen", open: "admin", closed: null },
  { id: CORE_PANEL_IDS.convert, flag: "viewerPanelOpen", open: "convert", closed: null },
];

beforeEach(() => {
  resetPanelRegistry();
  registerCorePanels();
  resetFlags();
});

test("registerCorePanels is idempotent and registers every core id under owner core", () => {
  registerCorePanels();
  const ids = listPanels().map((p) => p.id);
  assert.deepEqual(ids, Object.values(CORE_PANEL_IDS).filter((id) => ids.includes(id)));
  assert.deepEqual([...ids].sort(), Object.values(CORE_PANEL_IDS).sort());
  assert.ok(listPanels().every((p) => p.owner === "core" && p.binding));
  assert.equal(corePanelSpecs().length, ids.length);
});

test("the classic top bar's toggles are the registered toolbar panels, in their order", () => {
  assert.deepEqual(
    listToolbarPanels().map((p) => p.id),
    MENU_TOGGLES.map((t) => t.id),
  );
  // Plugin buttons sit between the last core toggle and the connection status.
  const order = (id: string) => getPanel(id)!.toolbar!.order;
  assert.ok(order(CORE_PANEL_IDS.cellbuilder) < PLUGIN_TOOLBAR_ORDER);
  assert.ok(order(CORE_PANEL_IDS.wsStatus) >= PLUGIN_TOOLBAR_ORDER);
});

test("the menu column's panels are registered, in the order the column stacks them", () => {
  const column = [
    CORE_PANEL_IDS.options,
    CORE_PANEL_IDS.storage,
    CORE_PANEL_IDS.serverInfo,
    CORE_PANEL_IDS.objectInfo,
    CORE_PANEL_IDS.scene,
    CORE_PANEL_IDS.wsStatus,
    CORE_PANEL_IDS.simulation,
    CORE_PANEL_IDS.componentControls,
    CORE_PANEL_IDS.cellbuilder,
    CORE_PANEL_IDS.equipmentAdmin,
    CORE_PANEL_IDS.systemAdmin,
    CORE_PANEL_IDS.simulations,
    CORE_PANEL_IDS.layers,
  ];
  const orders = column.map((id) => placementFor(getPanel(id)!, "classic"));
  assert.ok(orders.every((p) => p?.zone === "classic.menu-column"));
  const nums = orders.map((p) => p!.order!);
  assert.deepEqual([...nums].sort((a, b) => a - b), nums);
});

for (const t of MENU_TOGGLES) {
  test(`toggle("${t.id}") writes exactly what the classic button's store setter writes`, () => {
    for (const start of ["closed", "open"] as const) {
      resetFlags();
      if (start === "open") t.onClick();
      const before = flags();
      t.onClick();
      const byMenu = flags();

      resetFlags();
      if (start === "open") t.onClick();
      assert.deepEqual(flags(), before);
      toggle(t.id);
      const byRegistry = flags();

      assert.deepEqual(byRegistry, byMenu, `from ${start}`);
      const changed = Object.keys(before).filter((k) => before[k] !== byRegistry[k]);
      assert.deepEqual(changed, [t.flag], `from ${start}`);
    }
  });
}

for (const b of OTHER_BINDINGS) {
  test(`reveal / hide("${b.id}") write only ${b.flag}`, () => {
    const before = flags();
    reveal(b.id);
    const opened = flags();
    assert.equal(opened[b.flag], b.open);
    assert.deepEqual(
      Object.keys(before).filter((k) => before[k] !== opened[k]),
      [b.flag],
    );
    hide(b.id);
    assert.equal(flags()[b.flag], b.closed);
  });
}

test("admin and convert share one window: revealing one replaces the other", () => {
  reveal(CORE_PANEL_IDS.admin);
  reveal(CORE_PANEL_IDS.convert);
  assert.equal(useViewerPanelStore.getState().open, "convert");
  hide(CORE_PANEL_IDS.admin); // not the open one: no effect
  assert.equal(useViewerPanelStore.getState().open, "convert");
  assert.equal(getPanel(CORE_PANEL_IDS.admin)?.binding?.isOpen(), false);
});

test("where the layout lets them, admin and convert are open side by side and close one by one", () => {
  const s = () => useViewerPanelStore.getState();
  s().setExclusive(false);
  s().openPanel("admin", "audit_runs" as never);
  reveal(CORE_PANEL_IDS.convert);
  assert.deepEqual(s().shown, ["admin", "convert"]);
  assert.equal(s().open, "convert", "the last opened is in front");
  assert.equal(s().adminTab, "audit_runs", "opening convert leaves the admin tab alone");
  assert.equal(getPanel(CORE_PANEL_IDS.admin)?.binding?.isOpen(), true);
  hide(CORE_PANEL_IDS.convert);
  assert.deepEqual(s().shown, ["admin"]);
  assert.equal(s().open, "admin");
  reveal(CORE_PANEL_IDS.convert);
  s().setExclusive(true);
  assert.deepEqual(s().shown, ["convert"], "back to one at a time: the one in front stays");
  s().closePanel();
  assert.deepEqual([s().open, s().shown, s().adminTab], [null, [], null]);
});

test("a press brings the window behind to the front; one window alone stays as it is", () => {
  const s = () => useViewerPanelStore.getState();
  s().openPanel("admin");
  const alone = s();
  s().raisePanel("admin");
  assert.equal(s(), alone, "one window: no change");
  s().setExclusive(false);
  s().openPanel("convert");
  assert.deepEqual(s().shown, ["admin", "convert"]);
  s().raisePanel("admin");
  assert.deepEqual([s().open, s().shown], ["admin", ["convert", "admin"]]);
  const front = s();
  s().raisePanel("admin");
  assert.equal(s(), front, "already in front: no change");
  s().raisePanel("convert");
  assert.deepEqual(s().shown, ["admin", "convert"]);
});

test("closePanel used as a click handler closes every window", () => {
  const s = () => useViewerPanelStore.getState();
  s().openPanel("admin");
  s().closePanel({ type: "click" } as never);
  assert.equal(s().open, null);
});

test("availability follows the conditions the classic top bar gates on", () => {
  const ids = (env: PanelEnv) => listPanels(env).map((p) => p.id);
  // Hosted mode: storage + attribute search + admin/convert, no server-info box, no connection status.
  const rest = ids({ restMode: true });
  for (const id of [CORE_PANEL_IDS.storage, CORE_PANEL_IDS.attributeSearch, CORE_PANEL_IDS.admin]) {
    assert.ok(rest.includes(id), id);
  }
  assert.ok(!rest.includes(CORE_PANEL_IDS.serverInfo));
  assert.ok(!rest.includes(CORE_PANEL_IDS.wsStatus));
  // Desktop viewer: the opposite, and no connection status in a static embed.
  const ws = ids({ restMode: false, websocketDeactivated: false });
  assert.ok(ws.includes(CORE_PANEL_IDS.wsStatus) && ws.includes(CORE_PANEL_IDS.serverInfo));
  assert.ok(!ws.includes(CORE_PANEL_IDS.storage) && !ws.includes(CORE_PANEL_IDS.admin));
  assert.ok(!ids({ restMode: false, websocketDeactivated: true }).includes(CORE_PANEL_IDS.wsStatus));

  // Store-gated: nothing loaded -> no simulation, cellbuilder or component panel.
  const idle = ids({ restMode: true });
  assert.ok(!idle.includes(CORE_PANEL_IDS.simulation));
  assert.ok(!idle.includes(CORE_PANEL_IDS.cellbuilder));
  assert.ok(!idle.includes(CORE_PANEL_IDS.componentControls));
  useAnimationStore.setState({ hasAnimation: true });
  assert.ok(ids({ restMode: true }).includes(CORE_PANEL_IDS.simulation));
  useAnimationStore.setState({ hasAnimation: false });
});

test("panelActions registers the catalogue on first use", () => {
  resetPanelRegistry();
  assert.equal(listPanels().length, 0);
  assert.equal(panelActions.reveal(CORE_PANEL_IDS.scene), true);
  assert.equal(useSceneInfoStore.getState().show_scene_info_box, true);
  assert.equal(panelActions.isPanelOpen(CORE_PANEL_IDS.scene), true);
  panelActions.hide(CORE_PANEL_IDS.scene);
  assert.equal(useSceneInfoStore.getState().show_scene_info_box, false);
});
