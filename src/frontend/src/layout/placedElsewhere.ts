// Plugin panels the layout shows somewhere of its own: a group of the split grid
// or a window. Their region hosts (the menu column's plugin region, the
// Simulation and Admin tab strips, a core panel's plugin sections) skip them, so
// a panel is never drawn twice. Written by the layout frame, read by
// `getPanelsForRegion`.
//
// Dependency-free (a plain external store), so `@/plugins/registry` can read it.

let ids: ReadonlySet<string> = new Set();
let version = 0;
const listeners = new Set<() => void>();

/** Record the panels the layout places elsewhere. A no-op when the set is unchanged. */
export function setPlacedElsewhere(next: Iterable<string>): void {
  const set = new Set(next);
  if (set.size === ids.size && [...set].every((id) => ids.has(id))) return;
  ids = set;
  version++;
  for (const l of listeners) l();
}

export function isPlacedElsewhere(id: string): boolean {
  return ids.has(id);
}

/** For `useSyncExternalStore`: changes whenever the set does. */
export function placedElsewhereVersion(): number {
  return version;
}

export function subscribePlacedElsewhere(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}
