// The Desktop UI's menus: command ids only (core's commands, `panel:<id>` and
// `mode:<id>`), so the menu bar, the palette and the keys agree, and an item
// whose command is not offered right now (a REST-only panel on the desktop
// viewer, say) is left out by `MenuBar`. Pure, so it is tested without a DOM.

import type { MenuDef, MenuItemDef } from "../CommandPalette";
import {
  CAMERA_VIEWS,
  CORE_COMMAND_IDS as C,
  cameraViewCommandId,
  themeCommandId,
  uiShellCommandId,
  workspaceCommandId,
} from "../coreCommandIds";
import { CORE_PANEL_IDS as P } from "../corePanelIds";

const panel = (id: string) => `panel:${id}`;
const SEP: MenuItemDef = { separator: true };

/** What the menus list that changes at run time. */
export interface DesktopMenuInputs {
  /** The active shell's modes (`listShellModes()`), plugins' included. */
  modes: readonly { id: string }[];
  /** The saved workspaces (`listWorkspaces()`). */
  workspaces: readonly string[];
  /** The registered UI shells (`listUiShells()`). */
  shells: readonly { id: string }[];
  /** The panel theme presets (`THEME_PRESETS`). */
  themes: readonly string[];
}

/** The menu bar's menus. */
export function desktopMenus({ modes, workspaces, shells, themes }: DesktopMenuInputs): MenuDef[] {
  const menus: MenuDef[] = [
    {
      label: "File",
      items: [
        C.openFile,
        C.uploadFile,
        SEP,
        C.exportSelection,
        C.exportModel,
        SEP,
        panel(P.storage),
        panel(P.convert),
        SEP,
        panel(P.options),
      ],
    },
    {
      label: "Edit",
      items: [
        C.undo,
        C.redo,
        SEP,
        C.selectAll,
        C.invertSelection,
        C.clearSelection,
        C.copyNames,
        C.groupSelection,
        SEP,
        { label: "Visibility", items: [C.hideSelected, C.hideOthers, C.showAll] },
      ],
    },
    {
      label: "View",
      items: [
        {
          label: "Panels",
          items: [
            panel(P.tree),
            panel(P.objectInfo),
            panel(P.scene),
            panel(P.storage),
            panel(P.attributeSearch),
            panel(P.simulation),
            panel(P.componentControls),
            panel(P.cellbuilder),
            panel(P.nodeEditor),
          ],
        },
        SEP,
        { label: "Camera", items: [C.fitAll, C.fitSelection, C.resetCamera, SEP, ...CAMERA_VIEWS.map(cameraViewCommandId)] },
        { label: "Display", items: [C.edges, C.stats, C.gallery, SEP, C.toggleMarkingMenu] },
        // The loaded result's sets and properties (rows in the tree too); greyed, with the reason, without one.
        {
          label: "FEA sets",
          items: [C.feaIsolateSets, C.feaIsolateSelectionSets, C.feaClearIsolation, SEP, C.feaToggleContextWireframe],
        },
        { label: "Theme", items: themes.map(themeCommandId) },
        { label: "User interface", items: shells.map((s) => uiShellCommandId(s.id)) },
        SEP,
        C.commandPalette,
        C.markingMenu,
        C.fullscreen,
      ],
    },
  ];
  if (modes.length) menus.push({ label: "Modes", items: modes.map((m) => `mode:${m.id}`) });
  menus.push(
    {
      label: "Window",
      items: [
        C.newWindow,
        C.reopenWindows,
        SEP,
        C.resetLayout,
        C.resetAllLayouts,
        C.customiseRail,
        SEP,
        {
          label: "Workspaces",
          items: [C.saveWorkspace, ...(workspaces.length ? [SEP, ...workspaces.map(workspaceCommandId), SEP] : []), C.deleteWorkspace],
        },
      ],
    },
    {
      label: "Help",
      items: [C.shortcuts, SEP, panel(P.serverInfo), panel(P.wsStatus), SEP, C.about],
    },
  );
  return menus;
}

/** Every command id the menus name, nested ones included. */
export function menuCommandIds(menus: readonly MenuDef[]): string[] {
  const out: string[] = [];
  const walk = (items: readonly MenuItemDef[]) => {
    for (const it of items) {
      if (typeof it === "string") out.push(it);
      else if ("items" in it) walk(it.items);
    }
  };
  for (const m of menus) walk(m.items);
  return out;
}
