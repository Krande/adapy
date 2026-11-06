// Whether the served UI has a marking menu, whether it is on, and a way to open
// it from anywhere (its command, a shell's button). The menu itself is
// `./MarkingMenu`; the rules and the gesture are `./markingMenuModel`.
//
// The user's choice is kept per UI shell in the layout prefs
// (`LayoutPrefs.markingMenuByShell`), like "Customisable panels": turning it on
// in core's own UI leaves the Desktop UI as it was.

import { useMemo, useSyncExternalStore } from "react";
import { useStore } from "zustand";

import { activeUiShellId, activeUiShellVersion, CORE_UI_SHELL_ID, getUiShell, isComposedShell, subscribeActiveUiShell } from "@/plugins/uiShells";

import { layoutStore } from "./layoutStore";
import { markingMenuAvailability, type MarkingMenuConfig } from "./markingMenuModel";
import type { LayoutPrefs } from "./layout";

export interface ServedMarkingMenu {
  /** The shell id the user's choice is kept under. */
  shell: string;
  /** The served UI offers a marking menu at all. */
  available: boolean;
  /** It is on (available, and the user's choice or the shell's default). */
  on: boolean;
  config?: MarkingMenuConfig;
}

/** The marking menu of the served UI: core's own (off unless the user turns it on), a composed shell's (its `chrome.markingMenu`), none for a shell with a root of its own. */
export function servedMarkingMenu(prefs: Pick<LayoutPrefs, "markingMenuByShell"> = layoutStore.getState().prefs): ServedMarkingMenu {
  const shell = activeUiShellId();
  const spec = getUiShell(shell);
  const a =
    shell === CORE_UI_SHELL_ID
      ? markingMenuAvailability(undefined, false)
      : spec && isComposedShell(spec)
        ? markingMenuAvailability(spec.chrome, true)
        : { available: false, defaultOn: false };
  const own = prefs.markingMenuByShell?.[shell];
  return { shell, available: a.available, on: a.available && (typeof own === "boolean" ? own : a.defaultOn), config: a.config };
}

/** Turn it on or off in the served UI (ignored where there is none). */
export function setMarkingMenuOn(on: boolean): void {
  const m = servedMarkingMenu();
  if (!m.available) return;
  layoutStore.getState().setMarkingMenu(m.shell, on);
}

/** `servedMarkingMenu()`, re-rendering when the setting or the served UI changes. */
export function useServedMarkingMenu(): ServedMarkingMenu {
  const v = useSyncExternalStore(subscribeActiveUiShell, activeUiShellVersion, activeUiShellVersion);
  const byShell = useStore(layoutStore, (s) => s.prefs.markingMenuByShell);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  return useMemo(() => servedMarkingMenu({ markingMenuByShell: byShell }), [v, byShell]);
}

/** Where the host listens for an open request. */
let opener: ((at?: { x: number; y: number }) => boolean) | null = null;

/** The mounted menu's host takes open requests (one at a time); returns the undo. */
export function setMarkingMenuOpener(fn: ((at?: { x: number; y: number }) => boolean) | null): () => void {
  opener = fn;
  return () => {
    if (opener === fn) opener = null;
  };
}

/** Open the marking menu at `at` (client px), else at the pointer over the 3D view, else its centre. False where none is mounted and on. */
export function openMarkingMenu(at?: { x: number; y: number }): boolean {
  return opener ? opener(at) : false;
}

/** A host is mounted and listening. */
export function markingMenuMounted(): boolean {
  return opener !== null;
}
