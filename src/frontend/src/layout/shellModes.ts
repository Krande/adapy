// The modes of a composed shell: whole activities ("inspect", "results", a
// plugin's), each with its own arrangement of panels. The shell declares its own
// in `UiShellLayout.modes`; plugins add theirs (`PluginSpec.modes`). Core's own
// UI has none, and then nothing here is set.
//
// Dependency-free (a plain external store), so the facade root can export it;
// the React side is `./ShellModes`.

import type React from "react";

import type { AdaPluginContext } from "@/plugins/registry";

export interface ShellMode {
  id: string;
  label: string;
  hint?: string;
  icon?: React.FC;
  order: number;
  /** The plugin that contributed it; absent for the shell's own. */
  pluginId?: string;
  /** Core suspends the FEA field colouring while it is active (`PluginModeSpec.ownsSceneColor`). */
  ownsSceneColor?: boolean;
  /** False: core holds the FEA model undeformed while it is active. Default true. */
  showsDeformation?: boolean;
  /** The mode's own controls (`PluginModeSpec.toolbar`). */
  toolbar?: (ctx: AdaPluginContext) => React.ReactNode;
  /** Core toolbars drawn for the mode (`UiShellMode.toolbars`), before its own `toolbar`. */
  toolbars?: readonly string[];
  /** Offered only while this holds (`UiShellMode.requires`; `./modeRequirements`). */
  requires?: "builder";
}

/** Core's own mode toolbars (`./ModeToolbars`), by the id a mode's `toolbars` names. */
export const CORE_MODE_TOOLBAR_IDS = ["playback", "section", "representation", "colorBy", "build"] as const;
export type CoreModeToolbarId = (typeof CORE_MODE_TOOLBAR_IDS)[number];

/** A mode's badge in the switcher: a count, a dot, or none. */
export type ShellModeBadge = number | "dot";

let modes: readonly ShellMode[] = [];
let active: string | null = null;
let badges: Readonly<Record<string, ShellModeBadge>> = {};
let version = 0;
const listeners = new Set<() => void>();

function changed(): void {
  version++;
  for (const l of listeners) l();
}

/** Install a shell's modes, ordered by `(order, id)`; `initial` if it is one of them, else the first. */
export function setShellModes(list: readonly ShellMode[], initial?: string | null): void {
  modes = [...list].sort((a, b) => a.order - b.order || a.id.localeCompare(b.id));
  active = modes.find((m) => m.id === initial)?.id ?? modes[0]?.id ?? null;
  changed();
}

export function listShellModes(): readonly ShellMode[] {
  return modes;
}

/** The active mode's id; null when the shell has no modes. */
export function activeShellMode(): string | null {
  return active;
}

/** Switch mode. An id the shell does not have is ignored (and returns false). */
export function setShellMode(id: string): boolean {
  if (!modes.some((m) => m.id === id)) return false;
  if (active !== id) {
    active = id;
    changed();
  }
  return true;
}

/**
 * Mark a mode in the switcher: a count of things waiting there, a dot, or (null)
 * nothing. For a mode that is not the active one, so the user sees work elsewhere.
 */
export function setShellModeBadge(id: string, badge: ShellModeBadge | null): void {
  if ((badges[id] ?? null) === badge) return;
  const next = { ...badges };
  if (badge === null || badge === 0) delete next[id];
  else next[id] = badge;
  badges = next;
  changed();
}

export function shellModeBadge(id: string): ShellModeBadge | null {
  return badges[id] ?? null;
}

/** The URL parameter that opens a shell in a mode (`?mode=results`). */
export const SHELL_MODE_PARAM = "mode";

export function subscribeShellModes(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}

/** For `useSyncExternalStore`: changes whenever the modes or the active one do. */
export function shellModesVersion(): number {
  return version;
}

/** Is `panel` offered in the active mode: it names no modes, or names this one. */
export function offeredInMode(panel: { modes?: readonly string[] }): boolean {
  return !panel.modes || active === null || panel.modes.includes(active);
}
