// "UI freeze": lock the served UI's panel arrangement.
//
// Frozen, the panels stay where they are: the docking controls leave the headers of groups and
// windows (float, dock, move to a window, pin, collapse, "+" add, the "More actions" items that
// rearrange), a tab no longer drags and nothing is a drop target. Tabs still switch, dividers still
// resize, and panels can still be closed and opened again (a reopened panel goes where `reveal()`
// puts it).
//
// The setting is the user's PER UI SHELL (`LayoutPrefs.uiFreezeByShell`), like "Customisable panels".
// A shell opts out of it, or starts frozen, with `UiShellChrome.uiFreeze` (`false`, or `"default-on"`
// -- until the user chooses). The served shell is set by the classic preset's `setClassicShell`.

import { useSyncExternalStore } from "react";
import { useStore } from "zustand";

import type { LayoutPrefs } from "./layout";
import { layoutStore } from "./layoutStore";

/** What a shell says about it: `false` leaves the feature out, `"default-on"` starts frozen, else off until chosen. */
export type UiFreezeOption = boolean | "default-on";

const CORE_SHELL = "core";

let served: { id: string; option: UiFreezeOption } = { id: CORE_SHELL, option: true };
let version = 0;
const listeners = new Set<() => void>();

/** Record the served UI: shell `id` (core's own UI for null) and its `uiFreeze` option. */
export function setUiFreezeShell(id: string | null, option: UiFreezeOption | undefined): void {
  const next = { id: id ?? CORE_SHELL, option: option ?? true };
  if (next.id === served.id && next.option === served.option) return;
  served = next;
  version++;
  for (const l of listeners) l();
}

/** Does the served UI offer the freeze (its shell did not opt out)? */
export function uiFreezeOffered(): boolean {
  return served.option !== false;
}

/** The user's choice for shell `shell` in `prefs`, else the shell's default (off unless `"default-on"`). */
export function uiFreezeOn(prefs: Pick<LayoutPrefs, "uiFreezeByShell">, shell: string, option: UiFreezeOption = true): boolean {
  if (option === false) return false;
  const own = prefs.uiFreezeByShell?.[shell];
  return typeof own === "boolean" ? own : option === "default-on";
}

/** Is the served UI's layout frozen now. */
export function uiFrozen(prefs: Pick<LayoutPrefs, "uiFreezeByShell"> = layoutStore.getState().prefs): boolean {
  return uiFreezeOn(prefs, served.id, served.option);
}

/** Freeze or unfreeze the served UI's layout. Ignored where the shell opted out. */
export function setUiFrozen(on: boolean): void {
  if (!uiFreezeOffered()) return;
  layoutStore.getState().setUiFreeze(on, served.id);
}

const subscribe = (cb: () => void) => {
  listeners.add(cb);
  return () => {
    listeners.delete(cb);
  };
};
const current = () => version;

/** `uiFrozen()`, re-rendering when the setting or the served UI changes. */
export function useUiFrozen(): boolean {
  useSyncExternalStore(subscribe, current, current);
  return useStore(layoutStore, (s) => uiFrozen(s.prefs));
}

/** `uiFreezeOffered()`, re-rendering when the served UI changes. */
export function useUiFreezeOffered(): boolean {
  useSyncExternalStore(subscribe, current, current);
  return served.option !== false;
}
