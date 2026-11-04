// Bindings from a panel to the visibility flag that already exists for it.
//
// The classic UI keeps one boolean per panel in a domain store
// (`optionsStore.isOptionsVisible`, `sceneInfoStore.show_scene_info_box`, ...),
// and every existing opener writes it. A binding makes that flag the source of
// truth for the panel system too, rather than adding a second one to keep in
// sync: `reveal(id)` writes the same flag the top-bar button writes.
//
// Dependency-free: a store is taken structurally (`getState` + `subscribe`, which
// a zustand hook has), so this module imports no zustand.

import type { PanelBinding } from "./panel";

/** The part of a zustand store a binding needs. A `create()` hook satisfies it. */
export interface FlagStore<S> {
  getState(): S;
  subscribe(listener: (state: S, prev: S) => void): () => void;
}

/**
 * A spec's `watch` over the stores its `available` reads: notifies on any change
 * to any of them. Coarse on purpose -- `available` is cheap, and a host compares
 * the answers before it re-renders.
 */
export function watchStores(
  ...stores: ReadonlyArray<{ subscribe(listener: () => void): () => void }>
): (cb: () => void) => () => void {
  return (cb) => {
    const unsubs = stores.map((s) => s.subscribe(() => cb()));
    return () => {
      for (const u of unsubs) u();
    };
  };
}

/**
 * Bind a panel to a flag in a store.
 *
 * `read` derives "is the panel open" from the store state. `write` sets it,
 * through the store's own action wherever there is one, so that an action's side
 * effects (a refresh on open, say) still run.
 *
 * `write` is called on every `setOpen`, also when the flag already has that
 * value: that is what the call sites `reveal()` replaces did (`setX(true)`), so a
 * migrated call behaves exactly like the store call it replaced. A flag whose
 * only setter is a toggle checks the current value in its own `write`.
 *
 * `subscribe` notifies only when the derived value changes, not on every store
 * update.
 */
export function bindStoreFlag<S>(
  store: FlagStore<S>,
  read: (state: S) => boolean,
  write: (open: boolean, state: S) => void,
): PanelBinding {
  return {
    isOpen: () => !!read(store.getState()),
    setOpen: (open: boolean) => write(open, store.getState()),
    subscribe: (cb: () => void) =>
      store.subscribe((state, prev) => {
        if (!!read(state) !== !!read(prev)) cb();
      }),
  };
}
