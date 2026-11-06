// Plugin panels the layout shows somewhere of its own: a group of the split grid
// or a window of the layout -- or another browser window of a multi-window
// dashboard (`./windows`). Their region hosts (the menu column's plugin region,
// the Simulation and Admin tab strips, a core panel's plugin sections) skip
// them, so a panel is never drawn twice. Written by the layout frame and the
// window bus, read by `getPanelsForRegion`.
//
// Dependency-free (a plain external store), so `@/plugins/registry` can read it.

let layoutIds: ReadonlySet<string> = new Set();
let windowIds: ReadonlySet<string> = new Set();
let version = 0;
const listeners = new Set<() => void>();

const same = (a: ReadonlySet<string>, b: ReadonlySet<string>) => a.size === b.size && [...a].every((id) => b.has(id));

function changed(): void {
  version++;
  for (const l of listeners) l();
}

/** Record the panels the layout places elsewhere. A no-op when the set is unchanged. */
export function setPlacedElsewhere(next: Iterable<string>): void {
  const set = new Set(next);
  if (same(set, layoutIds)) return;
  layoutIds = set;
  changed();
}

/** Record the panels other browser windows show. A no-op when the set is unchanged. */
export function setPlacedInOtherWindows(next: Iterable<string>): void {
  const set = new Set(next);
  if (same(set, windowIds)) return;
  windowIds = set;
  changed();
}

export function isPlacedElsewhere(id: string): boolean {
  return layoutIds.has(id) || windowIds.has(id);
}

/** Is `id` shown by another browser window. */
export function isInOtherWindow(id: string): boolean {
  return windowIds.has(id);
}

/** For `useSyncExternalStore`: changes whenever the set does. */
export function placedElsewhereVersion(): number {
  return version;
}

export function subscribePlacedElsewhere(cb: () => void): () => void {
  listeners.add(cb);
  return () => listeners.delete(cb);
}
