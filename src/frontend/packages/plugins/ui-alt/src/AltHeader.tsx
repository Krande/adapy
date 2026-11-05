// The shell's own menu bar, drawn by core above the layout. Everything in it is
// core's: the mode switcher and the active mode's toolbar, the panel toggles,
// the plugins' top-bar buttons, the shell switcher. Only the styling is the shell's.

import React from "react";

import { UiShellSwitcher } from "@/viewer-core/app";
import { PanelToggles, ShellModeSwitcher, ShellModeToolbar } from "@/viewer-core/layout";
import { PluginTopBarButtons } from "@/viewer-core/plugins";

// Buttons are styled by the HOST shell, not by the panel or plugin — that is
// what keeps a feature plugin looking native in whichever UI mounts it.
const btn = (active: boolean) =>
  "inline-flex h-8 w-8 items-center justify-center rounded-sm text-white transition-colors " +
  (active ? "bg-emerald-600" : "bg-emerald-800/90 pointer-fine:hover:bg-emerald-700/90");

const AltHeader: React.FC = () => (
  <div className="flex min-h-11 shrink-0 flex-row items-center gap-2 border-b border-gray-700 bg-gray-950 px-2 py-1">
    <div className="rounded-sm bg-emerald-800/90 px-2 py-1 text-xs font-semibold text-white">Alt UI</div>
    <ShellModeSwitcher />
    <PanelToggles buttonClass={btn} />
    <ShellModeToolbar />
    <PluginTopBarButtons navBtnClass={btn} />
    <div className="ml-auto">
      <UiShellSwitcher className="rounded-sm bg-gray-800/90 px-2 py-1" />
    </div>
  </div>
);

export default AltHeader;
