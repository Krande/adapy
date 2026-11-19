// A composed shell's modes, for its header: the switcher and the active mode's
// own toolbar (a plugin mode's `toolbar`). Each mode keeps its own arrangement of
// panels, which the layout switches to with the mode (`./shellModes`).

import React, {useSyncExternalStore} from "react";

import {disablePlugin, type AdaPluginContext} from "@/plugins/registry";
import {makePluginContext} from "@/plugins/context";
import {useViewerStores} from "@/state/AdaViewerContext";
import {usePluginTheme} from "@/state/themeStore";

import {ModeToolbarRegion} from "./ModeToolbarRegion";
import {CoreModeToolbars} from "./ModeToolbars";
import {modeAvailable, modeRequirementsVersion, subscribeModeAvailability} from "./modeRequirements";

import {
  activeShellMode,
  listShellModes,
  setShellMode,
  shellModeBadge,
  shellModesVersion,
  subscribeShellModes,
  type ShellMode,
} from "./shellModes";

/** The modes on offer (those whose `requires` holds), the active one and the switch, re-rendering on change. */
export function useShellMode(): {modes: readonly ShellMode[]; active: string | null; setMode: (id: string) => boolean} {
  useSyncExternalStore(subscribeShellModes, shellModesVersion, shellModesVersion);
  useSyncExternalStore(subscribeModeAvailability, modeRequirementsVersion, modeRequirementsVersion);
  return {modes: listShellModes().filter(modeAvailable), active: activeShellMode(), setMode: setShellMode};
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

/** The active mode's own controls: the core toolbars it names (`toolbars`), then its `toolbar`; a throw disables that plugin. */
/**
 * The active mode's toolbars: the core toolbars its `toolbars` name, then its own `toolbar`. By default in
 * a region core owns (`ModeToolbarRegion`): it fills the room beside it, left-aligned, and clips behind a "»"
 * menu rather than push its neighbours. `region={false}` draws the bare toolbars, for a shell that lays them out itself.
 */
/**
 * A mode's controls: its own (`toolbar`, run as the plugin that has the mode), then a shell's for it
 * (`shellToolbar`, run as the shell). Each in its own context and its own try: what one throws
 * disables the plugin it came from, and the other's are still drawn.
 */
export function modeTools(
  mode: Pick<ShellMode, "id" | "toolbar" | "pluginId" | "shellToolbar" | "shellPluginId">,
  contextFor: (pluginId: string) => AdaPluginContext,
): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  const run = (
    toolbar: ShellMode["toolbar"],
    pluginId: string | undefined,
    what: string,
  ) => {
    if (!toolbar || !pluginId) return;
    try {
      parts.push(<React.Fragment key={what}>{toolbar(contextFor(pluginId))}</React.Fragment>);
    } catch (err) {
      disablePlugin(pluginId, `mode "${mode.id}" ${what} threw: ${String(err)}`);
    }
  };
  run(mode.toolbar, mode.pluginId, "toolbar");
  run(mode.shellToolbar, mode.shellPluginId, "shell toolbar");
  return parts;
}

/**
 * A shell's controls for the far right of a mode's toolbar region (`shellToolbarEnd`), run as the
 * shell; null when there are none, or when they threw (which disables the shell, as in `modeTools`).
 */
export function modeEndTools(
  mode: Pick<ShellMode, "id" | "shellToolbarEnd" | "shellPluginId">,
  contextFor: (pluginId: string) => AdaPluginContext,
): React.ReactNode {
  const {shellToolbarEnd: end, shellPluginId: pluginId} = mode;
  if (!end || !pluginId) return null;
  try {
    return end(contextFor(pluginId)) ?? null;
  } catch (err) {
    disablePlugin(pluginId, `mode "${mode.id}" shell toolbar end threw: ${String(err)}`);
    return null;
  }
}

export function ShellModeToolbar({region = true}: {region?: boolean}) {
  const {modes, active} = useShellMode();
  const stores = useViewerStores();
  const theme = usePluginTheme();
  const mode = modes.find((m) => m.id === active);
  if (!mode) return region ? <ModeToolbarRegion /> : null;
  const contextFor = (pluginId: string) => makePluginContext(pluginId, stores, theme);
  const tools = modeTools(mode, contextFor);
  const own: React.ReactNode = tools.length ? <>{tools}</> : null;
  const core = mode.toolbars?.length ? <CoreModeToolbars ids={mode.toolbars} /> : null;
  const end = modeEndTools(mode, contextFor);
  if (!core && !own && end == null) return region ? <ModeToolbarRegion /> : null;
  const content = (
    <>
      {core}
      {own}
    </>
  );
  if (region) return <ModeToolbarRegion end={end}>{content}</ModeToolbarRegion>;
  // Bare: the end controls follow the toolbars, and the shell lays them out itself.
  return (
    <>
      {content}
      {end}
    </>
  );
}
