// The catalogue of core's own panels: every panel the classic UI offers, in one
// readable list, each bound to the visibility flag it already has.
//
// The list used to exist only implicitly, as the toggle buttons and render
// conditions of a hand-written tree (since removed). This file states it once,
// and the layout host's classic preset (`./presets/classic`) renders the
// classic UI from it. Tests pin the catalogue to what that tree offered
// (`__tests__/layout/corePanels.test.ts`, `classicPreset.test.ts`).
//
// Each entry is an ADAPTER over today's component and flag, not a new panel:
//
//   * `body` loads the existing component, frame and all (`frame: "own"`), so
//     the classic preset mounts it without a visible change; `content` loads the
//     same panel's content without that frame, for a host that draws its own.
//   * `binding` reads and writes the existing flag, through the store's own
//     action where there is one.
//   * `available` mirrors the condition under which the classic UI offers the panel
//     (its button's `hidden`, or its render guard), read from the stores' current
//     state.
//
// Unlike `./registry`, this module imports the stores (zustand) and the runtime
// config, so it is NOT on the dependency-free facade root; it is registered at
// bootstrap by `@/plugins` `loadPlugins()`, and on first use by `./panelActions`.

import type React from "react";

import { runtime } from "@/runtime/config";
import { useAnimationStore } from "@/state/animationStore";
import { hasEmbeddedDoc, useCellBuilderStore } from "@/state/cellBuilderStore";
import { useComponentControlsStore } from "@/state/componentControlsStore";
import { useComponentSpecsStore } from "@/state/componentSpecsStore";
import { useConnectionBuilderStore } from "@/state/connectionBuilderStore";
import { useEquipmentCatalogStore } from "@/state/equipmentCatalogStore";
import { useFeaAnimationStore } from "@/state/feaAnimationStore";
import { useFemConceptsStore } from "@/state/femConceptsStore";
import { useObjectInfoStore } from "@/state/objectInfoStore";
import { useOptionsStore } from "@/state/optionsStore";
import { useSceneInfoStore } from "@/state/sceneInfoStore";
import { SEARCH_WINDOW_TITLE, useSearchWindowStore } from "@/state/searchWindowStore";
import { useServerInfoStore } from "@/state/serverInfoStore";
import { useTableNavStore } from "@/state/tableNavStore";
import { useTreeViewStore } from "@/state/treeViewStore";
import { useViewerPanelStore, type ViewerPanel } from "@/state/viewerPanelStore";
import { useWebsocketStatusStore } from "@/state/websocketStatusStore";
import {
  feModelLoaded,
  hasFemConcepts,
  simulationButtonShown,
} from "@/components/simulation/simPanelTabs";
import AttributeSearchIcon from "@/components/icons/AttributeSearchIcon";
import CellBuilderIcon from "@/components/icons/CellBuilderIcon";
import ComponentIcon from "@/components/icons/ComponentIcon";
import InfoIcon from "@/components/icons/InfoIcon";
import SceneIcon from "@/components/icons/SceneIcon";
import ServerIcon from "@/components/icons/ServerIcon";
import StorageIcon from "@/components/icons/StorageIcon";
import ToggleControlsIcon from "@/components/icons/AnimationControlToggle";
import TreeViewIcon from "@/components/icons/TreeViewIcon";

import { bindStoreFlag, watchStores } from "./bindings";
import { CLASSIC_PRESET_ID, CLASSIC_ZONES } from "./layout";
import type { PanelBinding, PanelBodyLoader, PanelPlacement, PanelProps, PanelSpec } from "./panel";
import { getPanel, registerPanel } from "./registry";
import { activeShellMode, subscribeShellModes } from "./shellModes";

import { CORE_PANEL_IDS, CORE_PANEL_OWNER, type CorePanelId } from "./corePanelIds";

export { CORE_PANEL_IDS, CORE_PANEL_OWNER, type CorePanelId };

/**
 * A panel's classic component (its content in its classic frame), or its
 * content alone. Loaded THROUGH the classic shell module (`@/app`, a chunk of its
 * own already), which resolves it from its own module graph -- statically
 * imported there, or lazily from inside it for the panels the classic UI has
 * always code-split. A dynamic import from here straight to a panel module would
 * give the bundler a second way to reach it that does not pass the shell, and it
 * would split everything the two share into chunks of their own. See
 * `./classicBodies`.
 */
function classicBody(id: CorePanelId): PanelBodyLoader {
  return () => import("@/app").then((m) => m.loadClassicPanel(id, "body"));
}

function classicContent(id: CorePanelId): PanelBodyLoader {
  return () => import("@/app").then((m) => m.loadClassicPanel(id, "content"));
}

/** The stores the Simulation button's condition reads. */
const watchSimulation = watchStores(useAnimationStore, useFeaAnimationStore, useFemConceptsStore);

/** A placement in the classic preset. */
const classic = (p: PanelPlacement) => ({ [CLASSIC_PRESET_ID]: p });
const column = (order: number) => classic({ zone: CLASSIC_ZONES.menuColumn, order });

// The conditions the classic UI gates its buttons and panels on, as plain reads.
const proceduralActive = () => {
  const s = useCellBuilderStore.getState();
  return s.active !== null || hasEmbeddedDoc(s);
};
const simulationOffered = () => {
  const fea = useFeaAnimationStore.getState();
  return simulationButtonShown({
    hasAnimation: useAnimationStore.getState().hasAnimation,
    sessionActive: fea.sessionActive,
    feModel: feModelLoaded({
      manifest: fea.manifest,
      femHasConcepts: hasFemConcepts(useFemConceptsStore.getState()),
    }),
  });
};

/** An in-viewer window: admin or convert, one at a time unless the layout lets both float. */
function viewerWindowBinding(panel: ViewerPanel): PanelBinding {
  return bindStoreFlag(
    useViewerPanelStore,
    (s) => s.shown.includes(panel),
    (open, s) => {
      if (open) s.openPanel(panel);
      else if (s.shown.includes(panel)) s.closePanel(panel);
    },
  );
}

/**
 * Core's panels, in the classic UI's top-bar order and then the rest. Built on
 * call (not at module load) so importing this module touches no store.
 */
export function corePanelSpecs(): PanelSpec[] {
  return [
    {
      id: CORE_PANEL_IDS.options,
      title: "Options",
      hint: "Toggle options drawer",
      shortcut: "Shift+Q",
      body: classicBody(CORE_PANEL_IDS.options),
      content: classicContent(CORE_PANEL_IDS.options),
      binding: bindStoreFlag(
        useOptionsStore,
        (s) => s.isOptionsVisible,
        (open, s) => s.setIsOptionsVisible(open),
      ),
      placement: column(10),
      toolbar: { order: 10 },
    },
    {
      id: CORE_PANEL_IDS.tree,
      title: "Selection tree",
      icon: TreeViewIcon,
      hint: "Show or hide the selection tree",
      shortcut: "Shift+T",
      body: classicBody(CORE_PANEL_IDS.tree),
      content: classicContent(CORE_PANEL_IDS.tree),
      binding: bindStoreFlag(
        useTreeViewStore,
        (s) => !s.isTreeCollapsed,
        (open, s) => s.setIsTreeCollapsed(!open),
      ),
      // Kept mounted once opened, as the classic drawer is: reopening must not rebuild the tree.
      // In a pop-out window it shows the main window's tree (`core:scene-tree`) and acts there.
      capabilities: { stackable: false, keepAlive: true, mirrored: true },
      constraints: { requiresViewerProvider: true },
      placement: classic({ zone: CLASSIC_ZONES.tree, order: 10 }),
      toolbar: { order: 20 },
    },
    {
      id: CORE_PANEL_IDS.storage,
      title: "Storage",
      icon: StorageIcon,
      hint: "Storage",
      body: classicBody(CORE_PANEL_IDS.storage),
      content: classicContent(CORE_PANEL_IDS.storage),
      binding: bindStoreFlag(
        useServerInfoStore,
        (s) => s.showServerInfoBox,
        (open, s) => s.setShowServerInfoBox(open),
      ),
      available: (env) => env.restMode !== false,
      constraints: { requiresRest: true },
      placement: column(20),
      toolbar: { order: 40 },
    },
    {
      // The same flag as Storage, on the desktop/websocket viewer, which has no
      // button for it: the classic column renders this box instead of the storage browser.
      id: CORE_PANEL_IDS.serverInfo,
      title: "Server info",
      icon: ServerIcon,
      body: classicBody(CORE_PANEL_IDS.serverInfo),
      content: classicContent(CORE_PANEL_IDS.serverInfo),
      binding: bindStoreFlag(
        useServerInfoStore,
        (s) => s.showServerInfoBox,
        (open, s) => s.setShowServerInfoBox(open),
      ),
      available: (env) => env.restMode !== true,
      placement: column(20),
    },
    {
      id: CORE_PANEL_IDS.attributeSearch,
      title: SEARCH_WINDOW_TITLE,
      icon: AttributeSearchIcon,
      hint: SEARCH_WINDOW_TITLE,
      body: classicBody(CORE_PANEL_IDS.attributeSearch),
      content: classicContent(CORE_PANEL_IDS.attributeSearch),
      binding: bindStoreFlag(
        useSearchWindowStore,
        (s) => s.open,
        (open, s) => (open ? s.show() : s.hide()),
      ),
      available: (env) => env.restMode !== false,
      constraints: { requiresRest: true },
      placement: classic({ zone: CLASSIC_ZONES.float, order: 10 }),
      toolbar: { order: 50 },
    },
    {
      id: CORE_PANEL_IDS.objectInfo,
      title: "Object info",
      icon: InfoIcon,
      hint: "Toggle object info",
      body: classicBody(CORE_PANEL_IDS.objectInfo),
      content: classicContent(CORE_PANEL_IDS.objectInfo),
      binding: bindStoreFlag(
        useObjectInfoStore,
        (s) => s.show_info_box,
        // The store's only writer is its toggle, which is what the button calls.
        (open, s) => {
          if (s.show_info_box !== open) s.toggle();
        },
      ),
      // In a pop-out window it shows the main window's selection (`windowMirrorCore`).
      capabilities: { mirrored: true },
      placement: column(30),
      toolbar: { order: 60 },
    },
    {
      id: CORE_PANEL_IDS.scene,
      title: "Scene info",
      icon: SceneIcon,
      hint: "Toggle scene info",
      body: classicBody(CORE_PANEL_IDS.scene),
      content: classicContent(CORE_PANEL_IDS.scene),
      binding: bindStoreFlag(
        useSceneInfoStore,
        (s) => s.show_scene_info_box,
        (open, s) => s.setShowSceneInfoBox(open),
      ),
      // In a pop-out window: the Model tab -- loaded models, take-off, groups, selection boxes
      // (`core:scene-info`); the tabs that act on the scene stay in the main window.
      capabilities: { mirrored: true },
      placement: column(40),
      toolbar: { order: 70 },
    },
    {
      id: CORE_PANEL_IDS.wsStatus,
      title: "Connection",
      hint: "Viewer connection status",
      body: classicBody(CORE_PANEL_IDS.wsStatus),
      content: classicContent(CORE_PANEL_IDS.wsStatus),
      binding: bindStoreFlag(
        useWebsocketStatusStore,
        (s) => s.showInfoBox,
        (open, s) => s.setShowInfoBox(open),
      ),
      available: (env) => env.restMode !== true && env.websocketDeactivated !== true,
      placement: column(50),
      toolbar: { order: 1000 },
    },
    {
      id: CORE_PANEL_IDS.simulation,
      title: "Simulation",
      icon: ToggleControlsIcon,
      hint: "Toggle simulation panel",
      body: classicBody(CORE_PANEL_IDS.simulation),
      content: classicContent(CORE_PANEL_IDS.simulation),
      watch: watchSimulation,
      binding: bindStoreFlag(
        useAnimationStore,
        (s) => s.isControlsVisible,
        (open, s) => s.setIsControlsVisible(open),
      ),
      available: () => simulationOffered(),
      // In a pop-out window it shows the main window's result session and drives it (core:fea-controls).
      capabilities: { mirrored: true },
      constraints: { requiresCanvas: true },
      placement: column(60),
      toolbar: { order: 80 },
    },
    {
      // The data table, toggled from inside the Simulation panel.
      id: CORE_PANEL_IDS.feaTable,
      title: "Result data",
      body: classicBody(CORE_PANEL_IDS.feaTable),
      content: classicContent(CORE_PANEL_IDS.feaTable),
      watch: watchSimulation,
      binding: bindStoreFlag(
        useTableNavStore,
        (s) => s.isPanelOpen,
        (open, s) => s.setPanelOpen(open),
      ),
      available: () => simulationOffered(),
      // In a pop-out window it lists rows the main window sends (core:fea-table).
      capabilities: { keepAlive: true, stackable: false, mirrored: true },
      placement: classic({ zone: CLASSIC_ZONES.menuColumn, parent: CORE_PANEL_IDS.simulation, as: "section" }),
    },
    {
      id: CORE_PANEL_IDS.componentControls,
      title: "Connection components",
      icon: ComponentIcon,
      hint: "Toggle connection-component panel",
      body: classicBody(CORE_PANEL_IDS.componentControls),
      content: classicContent(CORE_PANEL_IDS.componentControls),
      watch: watchStores(useComponentSpecsStore),
      binding: bindStoreFlag(
        useComponentControlsStore,
        (s) => s.isVisible,
        (open, s) => s.setIsVisible(open),
      ),
      available: () => useComponentSpecsStore.getState().hasSpecs,
      placement: column(70),
      toolbar: { order: 90 },
    },
    {
      // `ada dev connections`: opened at startup there, offered nowhere else; no top-bar button.
      id: CORE_PANEL_IDS.connectionBuilder,
      title: "Connection builder",
      icon: ComponentIcon,
      hint: "Toggle the connection builder",
      body: classicBody(CORE_PANEL_IDS.connectionBuilder),
      content: classicContent(CORE_PANEL_IDS.connectionBuilder),
      binding: bindStoreFlag(
        useConnectionBuilderStore,
        (s) => s.isVisible,
        (open, s) => s.setIsVisible(open),
      ),
      available: () => runtime.devConnections(),
      constraints: { requiresRest: true },
      placement: column(75),
    },
    {
      id: CORE_PANEL_IDS.cellbuilder,
      title: "Procedural cellbuilder",
      icon: CellBuilderIcon,
      hint: "Toggle procedural cellbuilder panel",
      body: classicBody(CORE_PANEL_IDS.cellbuilder),
      content: classicContent(CORE_PANEL_IDS.cellbuilder),
      watch: (cb) => {
        const stores = watchStores(useCellBuilderStore)(cb);
        const modes = subscribeShellModes(cb);
        return () => {
          stores();
          modes();
        };
      },
      binding: bindStoreFlag(
        useCellBuilderStore,
        (s) => s.panelVisible,
        (open, s) => s.setPanelVisible(open),
      ),
      // Also while the shell's Build mode is up with no model: the panel is where one is started.
      available: () => (proceduralActive() || activeShellMode() === "build"),
      constraints: { requiresCanvas: true },
      placement: column(80),
      toolbar: { order: 100 },
    },
    {
      // The builder's "Cells & equipment" list on its own, for a shell that docks it apart from the
      // builder. Offered with a procedural model; no top-bar button.
      id: CORE_PANEL_IDS.componentList,
      title: "Cells & equipment",
      icon: CellBuilderIcon,
      hint: "Toggle the procedural component list",
      body: classicBody(CORE_PANEL_IDS.componentList),
      content: classicContent(CORE_PANEL_IDS.componentList),
      watch: watchStores(useCellBuilderStore),
      binding: bindStoreFlag(
        useCellBuilderStore,
        (s) => s.componentListVisible,
        (open, s) => s.setComponentListVisible(open),
      ),
      available: () => proceduralActive(),
      constraints: { requiresCanvas: true },
      placement: column(85),
    },
    {
      // Opened contextually (an equipment's "Edit properties"); no top-bar button.
      id: CORE_PANEL_IDS.equipmentAdmin,
      title: "Equipment catalog",
      body: classicBody(CORE_PANEL_IDS.equipmentAdmin),
      content: classicContent(CORE_PANEL_IDS.equipmentAdmin),
      watch: watchStores(useCellBuilderStore),
      binding: bindStoreFlag(
        useEquipmentCatalogStore,
        (s) => s.equipmentPanelOpen,
        // Through the toggle, so opening refreshes the list as it always has.
        (open, s) => {
          if (s.equipmentPanelOpen !== open) s.toggleEquipmentPanel();
        },
      ),
      available: () => proceduralActive(),
      placement: column(90),
    },
    {
      id: CORE_PANEL_IDS.systemAdmin,
      title: "System catalog",
      body: classicBody(CORE_PANEL_IDS.systemAdmin),
      content: classicContent(CORE_PANEL_IDS.systemAdmin),
      watch: watchStores(useCellBuilderStore),
      binding: bindStoreFlag(
        useEquipmentCatalogStore,
        (s) => s.systemPanelOpen,
        (open, s) => {
          if (s.systemPanelOpen !== open) s.toggleSystemPanel();
        },
      ),
      available: () => proceduralActive(),
      placement: column(100),
    },
    {
      // The in-viewer admin window (Options' "Admin panel", Shift+P).
      id: CORE_PANEL_IDS.admin,
      title: "Admin panel",
      shortcut: "Shift+P",
      // Embedded, on the tab the viewer-panel store asks for, as the in-viewer window shows it.
      body: classicBody(CORE_PANEL_IDS.admin),
      binding: viewerWindowBinding("admin"),
      available: (env) => env.restMode !== false,
      constraints: { requiresRest: true, exclusiveGroup: "classic.viewer-window" },
      content: classicContent(CORE_PANEL_IDS.admin),
      placement: classic({ zone: CLASSIC_ZONES.window, order: 10, float: { w: 1100, h: 720 } }),
      // The classic chrome is the window host's, not the admin panel's own.
      frame: "host",
    },
    {
      id: CORE_PANEL_IDS.convert,
      title: "Convert files",
      body: classicBody(CORE_PANEL_IDS.convert),
      binding: viewerWindowBinding("convert"),
      available: (env) => env.restMode !== false,
      constraints: { requiresRest: true, exclusiveGroup: "classic.viewer-window" },
      content: classicContent(CORE_PANEL_IDS.convert),
      placement: classic({ zone: CLASSIC_ZONES.window, order: 20, float: { w: 1100, h: 720 } }),
      frame: "host",
    },
  ];
}

/** The facts `available` reads, from the runtime config. */
export function corePanelEnv(): { restMode: boolean; websocketDeactivated: boolean } {
  return { restMode: runtime.isRestMode(), websocketDeactivated: runtime.websocketDeactivated() };
}

/**
 * Register core's panels. Idempotent: the bootstrap calls it, and so does
 * `./panelActions` before its first intent, so a caller never finds the
 * catalogue empty.
 */
export function registerCorePanels(): void {
  if (getPanel(CORE_PANEL_IDS.options)) return;
  for (const spec of corePanelSpecs()) registerPanel(spec, CORE_PANEL_OWNER);
}
