// The built-in "Desktop" UI: a composed shell core ships next to the classic UI.
//
// A desktop-style menu bar in place of the classic top bar of buttons, docked
// panels around the 3D view (the tree on the left, the selected object and the
// scene on the right, results under the view), a status bar, and the command
// palette. Everything here is configuration of core's composed layout
// (`docs/UI_SHELLS.md`, "Everything a composed shell can configure"); the chrome
// draws core's commands (`CORE_COMMAND_IDS`, `panel:<id>`, `mode:<id>`).
//
// Registered by core at startup (`@/plugins` `loadPlugins`), so every build
// carries it; the classic UI stays the default. Kept free of heavy imports so the
// registration (and its tests) costs no scene or store code: the chrome loads
// only when this shell is the active one.

import { CORE_COMMAND_IDS, cameraViewCommandId } from "../coreCommandIds";
import { CORE_PANEL_IDS } from "../corePanelIds";
import { RAIL_SEPARATOR } from "../toolRailModel";
import type { GridNode } from "../splitTree";
import { getUiShell, registerUiShell, type UiShellSpec } from "@/plugins/uiShells";

/** The Desktop UI's shell id: what `?ui=` takes and the switcher stores. */
export const DESKTOP_UI_SHELL_ID = "desktop";

/** The menu bar's and the status bar's look: core's window tokens, the classic colours where unset. */
export const DESKTOP_BAR =
  "flex shrink-0 items-center gap-2 border-[var(--ada-window-border,#374151)] bg-[var(--ada-window-header-bg,#1f2937)] " +
  "text-[var(--ada-window-header-text,#f3f4f6)]";

/** The owner the shell registers under: core. */
const OWNER = "core";

/** The side docks: px widths a window resize leaves alone, which their dividers change within bounds. */
const LEFT = { px: 300, min: 200, max: 560 };
const RIGHT = { px: 340, min: 240, max: 640 };
/** The results dock under the view. */
const BOTTOM = { px: 260, min: 140, max: 640 };

/** Group ids, for `docks` and the tests. */
export const DESKTOP_GROUPS = {
  left: "left",
  rightTop: "right-top",
  rightBottom: "right-bottom",
  bottom: "bottom",
} as const;

const { tree, storage, objectInfo, scene, simulation, feaTable } = CORE_PANEL_IDS;

/** The view and the results dock under it; collapsed to a strip of icons in the Model mode. */
function centre(bottomCollapsed: boolean): GridNode {
  return {
    kind: "split",
    id: "s-centre",
    dir: "col",
    sizes: [0.72, 0.28],
    children: [
      { kind: "viewport" },
      {
        kind: "group",
        id: DESKTOP_GROUPS.bottom,
        panels: [simulation, feaTable],
        active: simulation,
        fixed: BOTTOM,
        ...(bottomCollapsed ? { collapsed: true } : {}),
      },
    ],
  };
}

/** The tree and storage on the left, the view (results under it), the selection and the scene on the right. */
function desktopGrid(bottomCollapsed: boolean): GridNode {
  return {
    kind: "split",
    id: "s-main",
    dir: "row",
    sizes: [0.2, 0.55, 0.25],
    children: [
      { kind: "group", id: DESKTOP_GROUPS.left, panels: [tree, storage], active: tree, fixed: LEFT },
      centre(bottomCollapsed),
      {
        kind: "split",
        id: "s-right",
        dir: "col",
        sizes: [0.55, 0.45],
        fixed: RIGHT,
        children: [
          { kind: "group", id: DESKTOP_GROUPS.rightTop, panels: [objectInfo], active: objectInfo },
          { kind: "group", id: DESKTOP_GROUPS.rightBottom, panels: [scene], active: scene, arrangement: "auto" },
        ],
      },
    ],
  };
}

/** The Model mode's grid: results dock collapsed. */
export const DESKTOP_GRID: GridNode = desktopGrid(true);
/** The Results mode's grid: results dock open. */
export const DESKTOP_RESULTS_GRID: GridNode = desktopGrid(false);

/**
 * The tool rail down the left edge, outside the docks: opening files, the
 * camera, visibility, selection, display, history. Users add, remove and
 * reorder tools; theirs is kept per shell (`LayoutPrefs.rail`).
 */
export const DESKTOP_RAIL_TOOLS: readonly string[] = [
  CORE_COMMAND_IDS.openFile,
  RAIL_SEPARATOR,
  CORE_COMMAND_IDS.fitAll,
  CORE_COMMAND_IDS.fitSelection,
  CORE_COMMAND_IDS.resetCamera,
  cameraViewCommandId("isometric"),
  RAIL_SEPARATOR,
  CORE_COMMAND_IDS.hideSelected,
  CORE_COMMAND_IDS.hideOthers,
  CORE_COMMAND_IDS.showAll,
  RAIL_SEPARATOR,
  CORE_COMMAND_IDS.selectAll,
  CORE_COMMAND_IDS.clearSelection,
  RAIL_SEPARATOR,
  CORE_COMMAND_IDS.edges,
  CORE_COMMAND_IDS.feaClearIsolation,
  RAIL_SEPARATOR,
  CORE_COMMAND_IDS.undo,
  CORE_COMMAND_IDS.redo,
];

/** Where plugin panels that name a dock go, the first time they are seen. */
const DOCKS = { left: DESKTOP_GROUPS.left, right: DESKTOP_GROUPS.rightBottom, bottom: DESKTOP_GROUPS.bottom };

/** The Desktop UI's spec. */
export function desktopUiShellSpec(): UiShellSpec {
  return {
    id: DESKTOP_UI_SHELL_ID,
    label: "Desktop",
    description: "A menu bar, a tool rail and docked panels around the 3D view, with a status bar.",
    order: 5,
    layout: {
      grid: DESKTOP_GRID,
      open: [tree, objectInfo, scene],
      docks: DOCKS,
      modes: [
        {
          id: "model",
          label: "Model",
          hint: "The model, its tree and properties",
          toolbars: ["section", "representation"],
        },
        {
          id: "results",
          label: "Results",
          hint: "Simulation results under the view",
          grid: DESKTOP_RESULTS_GRID,
          open: [tree, objectInfo, simulation],
          toolbars: ["playback", "representation"],
        },
      ],
      defaultMode: "model",
      // Raise when the arrangements above change, so saved ones start over.
      version: 1,
    },
    chrome: {
      header: () => import("./DesktopHeader"),
      footer: () => import("./DesktopFooter"),
      pageHeader: () => import("./DesktopPageHeader"),
      rail: { tools: DESKTOP_RAIL_TOOLS },
      // Core's wheel (hold the right button over the view, or Shift+E); on unless the user turns it off.
      markingMenu: {},
      toolbar: false,
      groups: { actions: ["add", "collapse", "pin"], tabIcons: true },
      commandPalette: { keys: "Ctrl+K" },
      overlays: { legend: "right-center", pluginPanels: "top-left" },
      toastAnchor: "bottom-right",
    },
  };
}

/** Register the Desktop UI (idempotent). */
export function registerDesktopUiShell(): void {
  if (!getUiShell(DESKTOP_UI_SHELL_ID)) registerUiShell(desktopUiShellSpec(), OWNER);
}
