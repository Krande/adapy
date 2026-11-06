// The shell's own menu bar, drawn by core above the layout. Everything in it is
// core's: the menus over core's commands (its panels, its modes, the shell's own
// commands), the mode switcher and the active mode's toolbar, the plugins'
// top-bar buttons, the command palette, the shell switcher. Only the styling and
// which menu holds what are the shell's.

import React from "react";

import { UiShellSwitcher } from "@/viewer-core/app";
import { CORE_PANEL_IDS } from "@/viewer-core";
import { MenuBar, ShellModeSwitcher, ShellModeToolbar, openCommandPalette, type MenuDef } from "@/viewer-core/layout";
import { PluginTopBarButtons } from "@/viewer-core/plugins";

// Buttons are styled by the HOST shell, not by the panel or plugin — that is
// what keeps a feature plugin looking native in whichever UI mounts it.
const btn = (active: boolean) =>
  "inline-flex h-8 w-8 items-center justify-center rounded-sm text-white transition-colors " +
  (active ? "bg-emerald-600" : "bg-emerald-800/90 pointer-fine:hover:bg-emerald-700/90");

// Menus are command ids: core's `panel:<id>` and `mode:<id>`, and the shell's own.
const MENUS: MenuDef[] = [
  {
    label: "View",
    items: [
      `panel:${CORE_PANEL_IDS.tree}`,
      `panel:${CORE_PANEL_IDS.objectInfo}`,
      `panel:${CORE_PANEL_IDS.scene}`,
      `panel:${CORE_PANEL_IDS.storage}`,
      `panel:${CORE_PANEL_IDS.options}`,
      { separator: true },
      "ui-alt:reset-layout",
      "core:command-palette",
    ],
  },
  { label: "Mode", items: ["mode:inspect", "mode:results", { separator: true }, "ui-alt:next-mode"] },
];

const AltHeader: React.FC = () => (
  <div className="flex min-h-11 shrink-0 flex-row items-center gap-2 border-b border-gray-700 bg-gray-950 px-2 py-1">
    <div className="rounded-sm bg-emerald-800/90 px-2 py-1 text-xs font-semibold text-white">Alt UI</div>
    <MenuBar menus={MENUS} />
    <div className="mx-auto flex items-center gap-2">
      <ShellModeSwitcher />
      <ShellModeToolbar />
    </div>
    <PluginTopBarButtons navBtnClass={btn} />
    <button type="button" className="rounded-sm bg-gray-800 px-2 py-1 text-xs text-gray-200 hover:bg-gray-700" onClick={openCommandPalette}>
      Commands <kbd className="text-gray-400">Shift+K</kbd>
    </button>
    <UiShellSwitcher className="rounded-sm bg-gray-800/90 px-2 py-1" />
  </div>
);

export default AltHeader;
