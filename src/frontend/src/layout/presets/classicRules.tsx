// The classic preset's rules, as data: its zones, when each panel is mounted,
// and how each core panel's top-bar toggle is drawn. Kept apart from the preset's
// components (`./classic`) so `node --test` can hold them to the classic UI without
// a canvas: they import icons and ids, no scene.

import React from "react";

import AttributeSearchIcon from "@/components/icons/AttributeSearchIcon";
import CellBuilderIcon from "@/components/icons/CellBuilderIcon";
import ComponentIcon from "@/components/icons/ComponentIcon";
import InfoIcon from "@/components/icons/InfoIcon";
import SceneIcon from "@/components/icons/SceneIcon";
import SimulationRunsIcon from "@/components/icons/SimulationRunsIcon";
import StorageIcon from "@/components/icons/StorageIcon";
import ToggleControlsIcon from "@/components/icons/AnimationControlToggle";
import TreeViewIcon from "@/components/icons/TreeViewIcon";
import {SEARCH_WINDOW_TITLE} from "@/state/searchWindowStore";

import {CORE_PANEL_IDS} from "../corePanels";
import {CLASSIC_PRESET_ID, CLASSIC_ZONES, type LayoutDoc, type ZoneDef} from "../layout";
import type {PanelEnv, PanelId, RegisteredPanel} from "../panel";
import type {PanelState} from "../panelStates";
import {emptyLayoutDoc} from "../reducers";

/** The classic preset's zones. The top bar is chrome, not a zone. */
export const CLASSIC_ZONE_DEFS: readonly ZoneDef[] = [
  {id: CLASSIC_ZONES.tree, kind: "dock", label: "Selection tree", edge: "left", limits: {min: 200, max: 560, default: 300}},
  {id: CLASSIC_ZONES.menuColumn, kind: "stack", label: "Panels", arrangement: "stack"},
  {id: CLASSIC_ZONES.float, kind: "float", label: "Windows"},
  {id: CLASSIC_ZONES.window, kind: "float", label: "Viewer window"},
  {id: CLASSIC_ZONES.overlay, kind: "overlay", label: "Canvas overlays"},
];

/**
 * The classic document a user starts from. Core's panels are bound to their own
 * flags, so it opens nothing; every registered panel counts as offered.
 */
export function classicDefaults({context, panels}: {context: string; panels: RegisteredPanel[]}): LayoutDoc {
  return {...emptyLayoutDoc(CLASSIC_PRESET_ID, CLASSIC_ZONE_DEFS, context), known: panels.map((p) => p.id)};
}

// ---------------------------------------------------------------------------
// Mount rules: the classic UI's render conditions, where they are not
// simply "the panel is open". Everything else mounts while open.
// ---------------------------------------------------------------------------

export interface MountCtx {
  state: PanelState;
  env: PanelEnv;
  /** A procedural model is loaded (an editable session or a document embedded in a GLB). */
  proceduralActive: boolean;
}

const MOUNT: Readonly<Record<PanelId, (c: MountCtx) => boolean>> = {
  // Always mounted: the drawer hides itself, and stays mounted once opened.
  [CORE_PANEL_IDS.tree]: () => true,
  // One flag, two panels: the storage browser in hosted mode, the server box on the desktop.
  [CORE_PANEL_IDS.storage]: ({state}) => state.open && state.available,
  [CORE_PANEL_IDS.serverInfo]: ({state}) => state.open && state.available,
  [CORE_PANEL_IDS.wsStatus]: ({state, env}) => state.open && env.websocketDeactivated !== true,
  // Mounted while a procedural model is loaded; the panel hides itself when toggled off,
  // and keeps its tab and popovers meanwhile.
  [CORE_PANEL_IDS.cellbuilder]: ({proceduralActive}) => proceduralActive,
  [CORE_PANEL_IDS.equipmentAdmin]: ({state, proceduralActive}) => proceduralActive && state.open,
  [CORE_PANEL_IDS.systemAdmin]: ({state, proceduralActive}) => proceduralActive && state.open,
  // Hosted-mode windows.
  [CORE_PANEL_IDS.attributeSearch]: ({state, env}) => env.restMode === true && state.open,
  [CORE_PANEL_IDS.admin]: ({state, env}) => env.restMode === true && state.open,
  [CORE_PANEL_IDS.convert]: ({state, env}) => env.restMode === true && state.open,
  [CORE_PANEL_IDS.simulations]: ({state, env}) => env.restMode === true && state.open,
};

/** Is the classic UI mounting this panel right now. */
export function classicMounts(id: PanelId, c: MountCtx): boolean {
  return MOUNT[id] ? MOUNT[id](c) : c.state.open;
}

// ---------------------------------------------------------------------------
// The top bar.
// ---------------------------------------------------------------------------

export interface ToolbarRecipe {
  /** The button's tooltip, by open state. */
  title: (open: boolean) => string;
  /** Its accessible name, when it has one beyond the title. */
  ariaLabel?: (open: boolean) => string;
  content: React.ReactNode;
  /** Left out entirely when the panel is not offered (rather than `hidden`). */
  omitWhenUnavailable?: boolean;
  /** Not a button: the connection status menu in a button-shaped box. */
  statusMenu?: boolean;
}

/** Each core toolbar panel's toggle, as the classic top bar draws it. */
export const CLASSIC_TOOLBAR: Readonly<Record<PanelId, ToolbarRecipe>> = {
  [CORE_PANEL_IDS.options]: {title: () => "Toggle options drawer", content: "☰"},
  [CORE_PANEL_IDS.tree]: {
    title: (open) => (open ? "Hide selection tree (Shift+T)" : "Show selection tree (Shift+T)"),
    ariaLabel: (open) => (open ? "Hide selection tree" : "Show selection tree"),
    content: <TreeViewIcon />,
  },
  [CORE_PANEL_IDS.storage]: {title: () => "Storage", content: <StorageIcon />},
  [CORE_PANEL_IDS.attributeSearch]: {
    title: () => SEARCH_WINDOW_TITLE,
    ariaLabel: () => SEARCH_WINDOW_TITLE,
    content: <AttributeSearchIcon />,
  },
  [CORE_PANEL_IDS.objectInfo]: {title: () => "Toggle object info", content: <InfoIcon />},
  [CORE_PANEL_IDS.scene]: {
    title: () => "Toggle scene info",
    ariaLabel: () => "Toggle scene info",
    content: <SceneIcon />,
  },
  [CORE_PANEL_IDS.simulation]: {title: () => "Toggle simulation panel", content: <ToggleControlsIcon />},
  [CORE_PANEL_IDS.componentControls]: {
    title: () => "Toggle connection-component panel",
    ariaLabel: () => "Toggle connection-component panel",
    content: <ComponentIcon />,
  },
  [CORE_PANEL_IDS.cellbuilder]: {
    title: () => "Toggle procedural cellbuilder panel",
    ariaLabel: () => "Toggle procedural cellbuilder panel",
    content: <CellBuilderIcon />,
  },
  [CORE_PANEL_IDS.simulations]: {
    title: () => "Toggle simulations panel",
    ariaLabel: () => "Toggle simulations panel",
    content: <SimulationRunsIcon />,
  },
  [CORE_PANEL_IDS.wsStatus]: {title: () => "Connection", content: null, omitWhenUnavailable: true, statusMenu: true},
};

/**
 * How a core panel's toggle appears: left out, present but `hidden` (so the row
 * does not reflow), or shown. A hosted-mode panel has no toggle at all
 * elsewhere; an unoffered one is hidden unless its recipe omits it.
 */
export function toolbarPresence(panel: RegisteredPanel, state: PanelState, env: PanelEnv): "omit" | "hidden" | "shown" {
  const recipe = CLASSIC_TOOLBAR[panel.id];
  if (!recipe) return "omit";
  if (panel.constraints?.requiresRest && env.restMode !== true) return "omit";
  if (!state.available) return recipe.omitWhenUnavailable ? "omit" : "hidden";
  return "shown";
}
