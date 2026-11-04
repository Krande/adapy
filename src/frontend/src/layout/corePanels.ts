// The catalogue of core's own panels: every panel the classic UI offers, in one
// readable list, each bound to the visibility flag it already has.
//
// Until now the list existed only implicitly, as the toggle buttons and render
// conditions in `components/Menu.tsx` and `app.tsx`. This file states it once.
// It changes nothing the classic UI renders: `Menu.tsx` still hand-writes its
// buttons and mounts its panels, reading the same flags these bindings read and
// write. A test holds the two together (`__tests__/layout/corePanels.test.ts`).
//
// Each entry is an ADAPTER over today's component and flag, not a new panel:
//
//   * `body` loads the existing component, frame and all (`frame: "own"`), so a
//     host can mount it later without a visible change. Content and frame are
//     split per panel in a later phase.
//   * `binding` reads and writes the existing flag, through the store's own
//     action where there is one.
//   * `available` mirrors the condition under which `Menu.tsx` offers the panel
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
import { useNodeEditorStore } from "@/state/useNodeEditorStore";
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
import GraphIcon from "@/components/icons/GraphIcon";
import InfoIcon from "@/components/icons/InfoIcon";
import SceneIcon from "@/components/icons/SceneIcon";
import ServerIcon from "@/components/icons/ServerIcon";
import ToggleControlsIcon from "@/components/icons/AnimationControlToggle";
import TreeViewIcon from "@/components/icons/TreeViewIcon";

import { bindStoreFlag } from "./bindings";
import { CLASSIC_PRESET_ID, CLASSIC_ZONES } from "./layout";
import type { PanelBinding, PanelBodyLoader, PanelPlacement, PanelProps, PanelSpec } from "./panel";
import { getPanel, registerPanel } from "./registry";

/** The owner id core registers its panels under. */
export const CORE_PANEL_OWNER = "core";

/** Stable ids of core's panels. */
export const CORE_PANEL_IDS = {
  options: "core:options",
  tree: "core:tree",
  nodeEditor: "core:node-editor",
  storage: "core:storage",
  serverInfo: "core:server-info",
  attributeSearch: "core:attribute-search",
  objectInfo: "core:object-info",
  scene: "core:scene",
  wsStatus: "core:ws-status",
  simulation: "core:simulation",
  feaTable: "core:fea-table",
  componentControls: "core:component-controls",
  cellbuilder: "core:cellbuilder",
  equipmentAdmin: "core:equipment-admin",
  systemAdmin: "core:system-admin",
  admin: "core:admin",
  convert: "core:convert",
} as const;

export type CorePanelId = (typeof CORE_PANEL_IDS)[keyof typeof CORE_PANEL_IDS];

/** A legacy component as a panel body. It ignores `PanelProps`, which a body may. */
function legacy(load: () => Promise<{ default: unknown }>): PanelBodyLoader {
  return () => load().then((m) => ({ default: m.default as React.ComponentType<PanelProps> }));
}

/** A component of the classic shell's own chunk, through `./classicBodies` (see there for why). */
function classicBody(name: keyof typeof import("./classicBodies")): PanelBodyLoader {
  return () =>
    import("./classicBodies").then((m) => ({ default: m[name] as React.ComponentType<PanelProps> }));
}

const classic = (p: PanelPlacement) => ({ [CLASSIC_PRESET_ID]: p });
const column = (order: number) => classic({ zone: CLASSIC_ZONES.menuColumn, order });

// The conditions Menu.tsx gates its buttons and panels on, as plain reads.
const nodeEditorOnly = () => useNodeEditorStore.getState().use_node_editor_only;
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

/** The in-viewer window shows one of admin / convert at a time. */
function viewerWindowBinding(panel: ViewerPanel): PanelBinding {
  return bindStoreFlag(
    useViewerPanelStore,
    (s) => s.open === panel,
    (open, s) => {
      if (open) s.openPanel(panel);
      else if (s.open === panel) s.closePanel();
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
      body: classicBody("OptionsComponent"),
      binding: bindStoreFlag(
        useOptionsStore,
        (s) => s.isOptionsVisible,
        (open, s) => s.setIsOptionsVisible(open),
      ),
      available: () => !nodeEditorOnly(),
      placement: column(10),
      toolbar: { order: 10 },
    },
    {
      id: CORE_PANEL_IDS.tree,
      title: "Selection tree",
      icon: TreeViewIcon,
      hint: "Show or hide the selection tree",
      shortcut: "Shift+T",
      body: classicBody("ResizableTreeView"),
      binding: bindStoreFlag(
        useTreeViewStore,
        (s) => !s.isTreeCollapsed,
        (open, s) => s.setIsTreeCollapsed(!open),
      ),
      available: () => !nodeEditorOnly(),
      capabilities: { stackable: false },
      constraints: { requiresViewerProvider: true },
      placement: classic({ zone: CLASSIC_ZONES.tree, order: 10 }),
      toolbar: { order: 20 },
    },
    {
      id: CORE_PANEL_IDS.nodeEditor,
      title: "Node editor",
      icon: GraphIcon,
      hint: "Toggle node editor",
      body: classicBody("NodeEditorComponent"),
      binding: bindStoreFlag(
        useNodeEditorStore,
        (s) => s.isNodeEditorVisible,
        (open, s) => s.setIsNodeEditorVisible(open),
      ),
      available: () => !nodeEditorOnly() && useOptionsStore.getState().enableNodeEditor,
      capabilities: { keepAlive: true, stackable: false },
      placement: classic({ zone: CLASSIC_ZONES.float, order: 20 }),
      toolbar: { order: 30 },
    },
    {
      id: CORE_PANEL_IDS.storage,
      title: "Storage",
      icon: ServerIcon,
      hint: "Storage",
      body: legacy(() => import("@/components/storage/StorageBrowser")),
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
      // button for it: Menu.tsx renders this box instead of the storage browser.
      id: CORE_PANEL_IDS.serverInfo,
      title: "Server info",
      icon: ServerIcon,
      body: classicBody("ServerInfoBox"),
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
      body: legacy(() => import("@/components/asset_browser/AttributeSearchWindow")),
      binding: bindStoreFlag(
        useSearchWindowStore,
        (s) => s.open,
        (open, s) => (open ? s.show() : s.hide()),
      ),
      available: (env) => env.restMode !== false && !nodeEditorOnly(),
      constraints: { requiresRest: true },
      placement: classic({ zone: CLASSIC_ZONES.float, order: 10 }),
      toolbar: { order: 50 },
    },
    {
      id: CORE_PANEL_IDS.objectInfo,
      title: "Object info",
      icon: InfoIcon,
      hint: "Toggle object info",
      body: classicBody("ObjectInfoBox"),
      binding: bindStoreFlag(
        useObjectInfoStore,
        (s) => s.show_info_box,
        // The store's only writer is its toggle, which is what the button calls.
        (open, s) => {
          if (s.show_info_box !== open) s.toggle();
        },
      ),
      available: () => !nodeEditorOnly(),
      placement: column(30),
      toolbar: { order: 60 },
    },
    {
      id: CORE_PANEL_IDS.scene,
      title: "Scene info",
      icon: SceneIcon,
      hint: "Toggle scene info",
      body: classicBody("SceneInfoBox"),
      binding: bindStoreFlag(
        useSceneInfoStore,
        (s) => s.show_scene_info_box,
        (open, s) => s.setShowSceneInfoBox(open),
      ),
      available: () => !nodeEditorOnly(),
      placement: column(40),
      toolbar: { order: 70 },
    },
    {
      id: CORE_PANEL_IDS.wsStatus,
      title: "Connection",
      hint: "Viewer connection status",
      body: classicBody("WebsocketStatusBox"),
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
      body: classicBody("SimulationControls"),
      binding: bindStoreFlag(
        useAnimationStore,
        (s) => s.isControlsVisible,
        (open, s) => s.setIsControlsVisible(open),
      ),
      available: () => simulationOffered(),
      constraints: { requiresCanvas: true },
      placement: column(60),
      toolbar: { order: 80 },
    },
    {
      // The data table, toggled from inside the Simulation panel.
      id: CORE_PANEL_IDS.feaTable,
      title: "Result data",
      body: classicBody("SimulationDataInfoPanel"),
      binding: bindStoreFlag(
        useTableNavStore,
        (s) => s.isPanelOpen,
        (open, s) => s.setPanelOpen(open),
      ),
      available: () => simulationOffered(),
      capabilities: { keepAlive: true, stackable: false },
      placement: classic({
        zone: CLASSIC_ZONES.menuColumn,
        parent: CORE_PANEL_IDS.simulation,
        as: "section",
      }),
    },
    {
      id: CORE_PANEL_IDS.componentControls,
      title: "Connection components",
      icon: ComponentIcon,
      hint: "Toggle connection-component panel",
      body: classicBody("ComponentControls"),
      binding: bindStoreFlag(
        useComponentControlsStore,
        (s) => s.isVisible,
        (open, s) => s.setIsVisible(open),
      ),
      available: () => !nodeEditorOnly() && useComponentSpecsStore.getState().hasSpecs,
      placement: column(70),
      toolbar: { order: 90 },
    },
    {
      id: CORE_PANEL_IDS.cellbuilder,
      title: "Procedural cellbuilder",
      icon: CellBuilderIcon,
      hint: "Toggle procedural cellbuilder panel",
      body: classicBody("CellBuilderPanel"),
      binding: bindStoreFlag(
        useCellBuilderStore,
        (s) => s.panelVisible,
        (open, s) => s.setPanelVisible(open),
      ),
      available: () => !nodeEditorOnly() && proceduralActive(),
      constraints: { requiresCanvas: true },
      placement: column(80),
      toolbar: { order: 100 },
    },
    {
      // Opened contextually (an equipment's "Edit properties"); no top-bar button.
      id: CORE_PANEL_IDS.equipmentAdmin,
      title: "Equipment catalog",
      body: legacy(() => import("@/components/admin/EquipmentAdminPanel")),
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
      body: legacy(() => import("@/components/admin/SystemAdminPanel")),
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
      body: legacy(() => import("@/components/admin/AdminPanel")),
      binding: viewerWindowBinding("admin"),
      available: (env) => env.restMode !== false,
      constraints: { requiresRest: true, exclusiveGroup: "classic.viewer-window" },
      placement: classic({ zone: CLASSIC_ZONES.window, order: 10, float: { w: 1100, h: 720 } }),
      // The classic chrome is the window host's, not the admin panel's own.
      frame: "host",
    },
    {
      id: CORE_PANEL_IDS.convert,
      title: "Convert files",
      body: legacy(() => import("@/components/convert/ConvertPage")),
      binding: viewerWindowBinding("convert"),
      available: (env) => env.restMode !== false,
      constraints: { requiresRest: true, exclusiveGroup: "classic.viewer-window" },
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
