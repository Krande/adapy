// "Customisable panels" -- floating, docking and stacking panels around the view --
// as the served UI has it. The one place every reader asks (the classic preset's
// zones, the grid, the windows, the zone keys, the shortcuts dialog, Options).
//
// The setting is the user's PER UI SHELL (`LayoutPrefs.customisablePanelsByShell`):
// turning it off in core's own UI leaves the other UIs alone. A composed shell's
// docked layout is built on it, so a composed shell REQUIRES it
// (`UiShellLayout.customisablePanels`, default "required"): there it is always on
// and Options shows the toggle disabled. Core's own UI is "optional".
//
// The served shell is set by the classic preset's `setClassicShell` (at startup,
// and again by an in-place shell switch).

import { useSyncExternalStore } from "react";
import { useStore } from "zustand";

import { customisablePanelsOn, type LayoutPrefs } from "./layout";
import { layoutStore } from "./layoutStore";

/** Core's own UI's shell id (`CORE_UI_SHELL_ID`), whose setting is the user's to choose. */
const CORE_SHELL = "core";

let served: { id: string; required: boolean } = { id: CORE_SHELL, required: false };
let version = 0;
const listeners = new Set<() => void>();

/** Record the served UI: shell `id` (core's own UI for null), and whether it requires customisable panels. */
export function setCustomisableShell(id: string | null, required: boolean): void {
  const next = { id: id ?? CORE_SHELL, required: !!id && required };
  if (next.id === served.id && next.required === served.required) return;
  served = next;
  version++;
  for (const l of listeners) l();
}

/** The id the setting is kept under for the served UI. */
export function customisableShellId(): string {
  return served.id;
}

/** Does the served UI require customisable panels (a composed shell): the setting is then not the user's. */
export function customisablePanelsRequired(): boolean {
  return served.required;
}

/** Are customisable panels on in the served UI: always in a shell that requires them, else the user's setting for it. */
export function customisablePanels(prefs: Pick<LayoutPrefs, "customisablePanels" | "customisablePanelsByShell"> = layoutStore.getState().prefs): boolean {
  return served.required || customisablePanelsOn(prefs, served.id);
}

/** Set the user's choice for the served UI. Ignored where the UI requires them. */
export function setCustomisablePanels(on: boolean): void {
  if (served.required) return;
  layoutStore.getState().setCustomisablePanels(on, served.id);
}

const subscribe = (cb: () => void) => {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
};
const current = () => version;

/** `customisablePanels()`, re-rendering when the setting or the served UI changes. */
export function useCustomisablePanels(): boolean {
  useSyncExternalStore(subscribe, current, current);
  return useStore(layoutStore, (s) => customisablePanels(s.prefs));
}

/** Whether the served UI requires them, re-rendering when the served UI changes. */
export function useCustomisablePanelsRequired(): boolean {
  useSyncExternalStore(subscribe, current, current);
  return served.required;
}
