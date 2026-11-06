// A composed shell's modes, for its header: the switcher and the active mode's
// own toolbar (a plugin mode's `toolbar`). Each mode keeps its own arrangement of
// panels, which the layout switches to with the mode (`./shellModes`).

import React, {useSyncExternalStore} from "react";

import {disablePlugin} from "@/plugins/registry";
import {makePluginContext} from "@/plugins/context";
import {useViewerStores} from "@/state/AdaViewerContext";
import {usePluginTheme} from "@/state/themeStore";

import {
  activeShellMode,
  listShellModes,
  setShellMode,
  shellModeBadge,
  shellModesVersion,
  subscribeShellModes,
  type ShellMode,
} from "./shellModes";

/** The modes, the active one and the switch, re-rendering on change. */
export function useShellMode(): {modes: readonly ShellMode[]; active: string | null; setMode: (id: string) => boolean} {
  useSyncExternalStore(subscribeShellModes, shellModesVersion, shellModesVersion);
  return {modes: listShellModes(), active: activeShellMode(), setMode: setShellMode};
}

export interface ShellModeSwitcherProps {
  /** A mode button's classes by active state. */
  buttonClass?: (active: boolean) => string;
  className?: string;
}

const DEFAULT_BUTTON = (active: boolean) =>
  "inline-flex h-8 items-center gap-1.5 rounded-sm px-2 text-xs font-semibold transition-colors " +
  (active ? "bg-blue-700 text-white" : "bg-gray-800 text-gray-200 pointer-fine:hover:bg-gray-700");

/** One button per mode; nothing when the shell has none. */
export function ShellModeSwitcher({buttonClass = DEFAULT_BUTTON, className = "flex items-center gap-1"}: ShellModeSwitcherProps) {
  const {modes, active, setMode} = useShellMode();
  if (!modes.length) return null;
  return (
    <div className={className} role="group" aria-label="Mode">
      {modes.map((m) => {
        const Icon = m.icon;
        const on = m.id === active;
        const badge = shellModeBadge(m.id);
        const note = badge === null ? "" : badge === "dot" ? " (something new here)" : ` (${badge} waiting)`;
        return (
          <button
            key={m.id}
            type="button"
            className={`relative ${buttonClass(on)}`}
            aria-pressed={on}
            title={(m.hint ?? m.label) + note}
            aria-label={m.label + note}
            onClick={() => setMode(m.id)}
          >
            {Icon && <Icon />}
            <span>{m.label}</span>
            {badge !== null && (
              <span
                aria-hidden="true"
                className={
                  badge === "dot"
                    ? "absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-[var(--ada-accent,#3b82f6)]"
                    : "absolute -right-1.5 -top-1.5 min-w-4 rounded-full bg-[var(--ada-accent,#3b82f6)] px-1 text-[10px] leading-4 text-white"
                }
              >
                {badge === "dot" ? null : badge}
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}

/** The active mode's own controls, when a plugin's mode has them; a throw disables that plugin. */
export function ShellModeToolbar() {
  const {modes, active} = useShellMode();
  const stores = useViewerStores();
  const theme = usePluginTheme();
  const mode = modes.find((m) => m.id === active);
  if (!mode?.toolbar || !mode.pluginId) return null;
  try {
    return <>{mode.toolbar(makePluginContext(mode.pluginId, stores, theme))}</>;
  } catch (err) {
    disablePlugin(mode.pluginId, `mode "${mode.id}" toolbar threw: ${String(err)}`);
    return null;
  }
}
