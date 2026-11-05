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
  /** The mode's own controls (`PluginModeSpec.toolbar`). */
  toolbar?: (ctx: AdaPluginContext) => React.ReactNode;
}

let modes: readonly ShellMode[] = [];
let active: string | null = null;
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
