// The served shell's tool rail as the user has it, the edits that save it, and
// whether its customise dialog is open. The rules are `./toolRailModel`; the rail and
// the dialog are `./ToolRail`.

import { useMemo, useSyncExternalStore } from "react";
import { useStore } from "zustand";

import { activeUiShellId, getUiShell, isComposedShell } from "@/plugins/uiShells";

import { layoutStore } from "./layoutStore";
import { dedupeRail, railConfig, railIsDefault, railTools, tidyRail, type RailPrefs, type ToolRailConfig } from "./toolRailModel";

/** The active shell's rail when it is core's (`chrome.rail: {tools}`), with the shell's id. */
export function activeRail(): { shell: string; config: ToolRailConfig } | null {
  const shell = activeUiShellId();
  const spec = getUiShell(shell);
  if (!spec || !isComposedShell(spec)) return null;
  const config = railConfig(spec.chrome?.rail);
  return config ? { shell, config } : null;
}

/** The user's saved rail in `shell`, if any. */
export function savedRail(shell: string): RailPrefs | null {
  return layoutStore.getState().prefs.rail?.[shell] ?? null;
}

/** The rail the user sees in `shell` (separators included, before the unoffered tools are left out). */
export function railList(shell: string, defaults: readonly string[]): string[] {
  return railTools(defaults, savedRail(shell));
}

/** Keep `tools` as the user's rail in `shell`; the shell's own list again when they are the same. */
export function saveRail(shell: string, defaults: readonly string[], tools: readonly string[]): void {
  const prefs: RailPrefs = { tools: dedupeRail(tools), base: tidyRail(defaults) };
  layoutStore.getState().setRail(shell, railIsDefault(defaults, prefs) ? null : prefs);
}

/** Back to the shell's own rail. */
export function resetRail(shell: string): void {
  layoutStore.getState().setRail(shell, null);
}

/** `railList`, re-rendering when the user's rail changes. */
export function useRailList(shell: string, defaults: readonly string[]): string[] {
  const saved = useStore(layoutStore, (s) => s.prefs.rail?.[shell]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => railTools(defaults, saved), [saved, defaults.join("\u0000")]);
}

// The customise dialog's open state: the rail's menu, its button and a command open it.
let dialogOpen = false;
const listeners = new Set<() => void>();
const setDialog = (v: boolean) => {
  if (dialogOpen === v) return;
  dialogOpen = v;
  for (const l of listeners) l();
};

export function openRailCustomise(): void {
  setDialog(true);
}

export function closeRailCustomise(): void {
  setDialog(false);
}

export function useRailCustomiseOpen(): boolean {
  return useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    () => dialogOpen,
    () => false,
  );
}
