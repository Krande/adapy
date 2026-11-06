// The Desktop UI's menu bar, drawn by core above the layout in place of the
// classic top bar of buttons: File, Edit, View, Modes, Window and Help over
// core's commands, the mode switcher with the active plugin mode's toolbar, the
// plugins' top-bar buttons and the command palette's button.

import React from "react";

import {PluginTopBarButtons} from "@/plugins/PluginSlots";
import {listUiShells} from "@/plugins/uiShells";
import {THEME_PRESETS} from "@/state/themeStore";

import {MenuBar, openCommandPalette} from "../CommandPalette";
import {useWorkspaceNames} from "../coreCommands";
import {ShellModeSwitcher, ShellModeToolbar, useShellMode} from "../ShellModeSwitcher";
import {desktopMenus} from "./desktopMenus";
import {DESKTOP_BAR} from "./desktopShell";

const MODE_BUTTON = (active: boolean) =>
  "inline-flex h-6 items-center rounded-sm px-2 text-xs transition-colors " +
  (active ? "bg-[var(--ada-tab-active-bg,#374151)] text-[var(--ada-tab-active-text,#ffffff)]" : "text-[var(--ada-tab-text,#d1d5db)] hover:bg-white/10");

const PLUGIN_BUTTON = (active: boolean) =>
  "inline-flex h-7 w-7 items-center justify-center rounded-sm transition-colors " +
  (active ? "bg-[var(--ada-tab-active-bg,#374151)]" : "hover:bg-white/10");

const THEMES = Object.keys(THEME_PRESETS);

/** The menus, rebuilt when the modes or the saved workspaces change. */
export function useDesktopMenus() {
  const {modes} = useShellMode();
  const workspaces = useWorkspaceNames();
  return React.useMemo(() => desktopMenus({modes, workspaces, shells: listUiShells(), themes: THEMES}), [modes, workspaces]);
}

const DesktopHeader: React.FC = () => {
  const menus = useDesktopMenus();
  return (
    <div className={`${DESKTOP_BAR} h-9 border-b px-1`} data-desktop-header="">
      <MenuBar menus={menus} />
      <div className="mx-auto flex items-center gap-2">
        <ShellModeSwitcher buttonClass={MODE_BUTTON} className="flex items-center gap-0.5" />
        <ShellModeToolbar />
      </div>
      <div className="flex items-center gap-1">
        <PluginTopBarButtons navBtnClass={PLUGIN_BUTTON} />
      </div>
      <button
        type="button"
        className="rounded-sm px-2 py-1 text-xs hover:bg-white/10"
        title="Search every command"
        onClick={openCommandPalette}
      >
        Commands <kbd className="text-[var(--ada-panel-text-muted,#9ca3af)]">Ctrl+K</kbd>
      </button>
    </div>
  );
};

export default DesktopHeader;
