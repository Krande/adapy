// UI behaviours: what core does that one UI wants and another might not, by name, with each UI
// choosing for itself.
//
// Core serves several UIs (shells): its own classic UI (`"core"`), the built-in Desktop UI and the
// shells plugins bring. A change to how core's shared code behaves reaches all of them at once, so
// a change one UI asked for can alter another's. A behaviour that may differ between UIs is
// therefore a named entry here, with a default for core's own UI and one for every other shell,
// and a shell sets its own with `UiShellSpec.behaviours` (`{id: true | false}`). Core's code asks
// `uiBehaviour(id)` -- the active shell's value, else the default -- instead of asking which UI is
// up.
//
// THE RULE for a new entry: its defaults keep every UI as it was, and a UI that wants the new
// behaviour opts in from its own spec. An existing behaviour moved behind an entry keeps its
// current behaviour as its defaults, so the move changes nothing. Changing a default is a change
// to every UI that has not chosen; the tests pin the defaults so it shows in a review.
//
// Dependency-free apart from the shell registry (the facade root exports it).

import { useSyncExternalStore } from "react";

import { activeUiShellId, activeUiShellVersion, CORE_UI_SHELL_ID, getUiShell, subscribeActiveUiShell } from "./uiShells";

/** One behaviour: what it does when on, and what each kind of UI gets without choosing. */
export interface UiBehaviourDef {
  /** What core does while it is on, in a sentence. */
  description: string;
  /** Core's own (classic) UI, shell `"core"`. */
  core: boolean;
  /** Every other shell (the Desktop UI, a plugin's) that does not set it. */
  shells: boolean;
}

/** Every behaviour a UI may set. */
export const UI_BEHAVIOURS = {
  "builder.pickHiddenWhileCellsHidden": {
    description:
      "The Properties panel leaves out a cell builder pick (a cell, a ref) while the builder's cells are not drawn: " +
      "in a mode that keeps the procedural model open behind it, and in the builder's own Sim and Detail views after a compile.",
    core: true,
    shells: true,
  },
} as const satisfies Record<string, UiBehaviourDef>;

export type UiBehaviourId = keyof typeof UI_BEHAVIOURS;

/** A shell's own choices (`UiShellSpec.behaviours`): any behaviour it leaves out gets the default. */
export type UiShellBehaviours = Partial<Record<UiBehaviourId, boolean>>;

/** Is `id` a behaviour this core has (a shell built for a newer core may name one it does not). */
export function isUiBehaviour(id: string): id is UiBehaviourId {
  return Object.prototype.hasOwnProperty.call(UI_BEHAVIOURS, id);
}

/** What shell `shellId` gets for `id`: its own choice, else the default for its kind of UI. */
export function uiBehaviourFor(shellId: string, id: UiBehaviourId): boolean {
  const own = getUiShell(shellId)?.behaviours?.[id];
  if (typeof own === "boolean") return own;
  const def: UiBehaviourDef = UI_BEHAVIOURS[id];
  return shellId === CORE_UI_SHELL_ID ? def.core : def.shells;
}

/** Is behaviour `id` on in the UI on screen. */
export function uiBehaviour(id: UiBehaviourId): boolean {
  return uiBehaviourFor(activeUiShellId(), id);
}

/** `uiBehaviour(id)`, re-rendering when an in-place switch changes the UI on screen. */
export function useUiBehaviour(id: UiBehaviourId): boolean {
  useSyncExternalStore(subscribeActiveUiShell, activeUiShellVersion, activeUiShellVersion);
  return uiBehaviour(id);
}

/** The names a shell's `behaviours` gives that this core does not have (they are ignored). */
export function unknownUiBehaviours(behaviours: Record<string, unknown> | undefined): string[] {
  return Object.keys(behaviours ?? {}).filter((id) => !isUiBehaviour(id));
}
