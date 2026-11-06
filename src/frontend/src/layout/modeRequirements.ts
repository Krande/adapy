// What a shell mode can require before it is offered (`UiShellMode.requires`).
// Today one thing: `"builder"`, a procedural model open in the builder. A mode
// that requires something is left out of the switcher, the Modes menu and the
// palette while it is missing, and the shell falls back to another mode if it
// was the active one when the requirement went away.

import {hasEmbeddedDoc, useCellBuilderStore} from "@/state/cellBuilderStore";

/** The requirements a mode may name. */
export type ModeRequirement = "builder";

/** Is a procedural model open: an editable session, or one read off a loaded scene? */
export function builderLoaded(): boolean {
  const s = useCellBuilderStore.getState();
  return s.active !== null || hasEmbeddedDoc(s);
}

/** Does the mode's requirement hold now? A mode that requires nothing is always available. */
export function modeAvailable(mode: {requires?: ModeRequirement}): boolean {
  return mode.requires === "builder" ? builderLoaded() : true;
}

/** Tell `cb` when what a requirement reads may have changed; returns the unsubscribe. */
export function subscribeModeRequirements(cb: () => void): () => void {
  let was = builderLoaded();
  return useCellBuilderStore.subscribe(() => {
    const now = builderLoaded();
    if (now !== was) {
      was = now;
      cb();
    }
  });
}

let version = 0;
let attached: (() => void) | null = null;
const listeners = new Set<() => void>();

/** For `useSyncExternalStore`: changes whenever a requirement's answer does. */
export function modeRequirementsVersion(): number {
  return version;
}

/** Subscribe to requirement changes (one store subscription shared by all listeners). */
export function subscribeModeAvailability(cb: () => void): () => void {
  listeners.add(cb);
  if (!attached) {
    attached = subscribeModeRequirements(() => {
      version++;
      for (const l of listeners) l();
    });
  }
  return () => {
    listeners.delete(cb);
    if (!listeners.size && attached) {
      attached();
      attached = null;
    }
  };
}
