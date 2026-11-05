// Scene loads whose progress a surface tells itself (a storage row, say), so the progress
// toast leaves them out instead of reporting one load twice.
//
// Dependency-free, so it sits on the root `@/viewer-core` entry. Claims are counted: a
// name stays claimed until every claim on it is released.

/** Conversion-store targets of a scene load: its conversion and its streaming bake. */
export const LOAD_JOB_TARGETS = ["glb", "fea"] as const;

const counts = new Map<string, number>();
const listeners = new Set<() => void>();
let version = 0;

function changed(): void {
  version++;
  for (const l of [...listeners]) l();
}

/** Claims the load of source `name`; returns the release (calling it twice releases once). */
export function claimLoad(name: string): () => void {
  counts.set(name, (counts.get(name) ?? 0) + 1);
  changed();
  let released = false;
  return () => {
    if (released) return;
    released = true;
    releaseLoad(name);
  };
}

/** Releases one claim on `name`. */
export function releaseLoad(name: string): void {
  const n = counts.get(name);
  if (n === undefined) return;
  if (n > 1) counts.set(name, n - 1);
  else counts.delete(name);
  changed();
}

/** Is the load of `name` claimed? With `jobKey`, only that source's own load jobs
 *  (`<name>::glb`, `<name>::fea`) are; any other job on it stays the toast's. */
export function isLoadClaimed(name: string, jobKey?: string): boolean {
  if (!counts.has(name)) return false;
  return jobKey === undefined || LOAD_JOB_TARGETS.some((t) => jobKey === `${name}::${t}`);
}

/** Calls `onChange` on every claim or release; returns the unsubscribe. */
export function subscribeLoadClaims(onChange: () => void): () => void {
  listeners.add(onChange);
  return () => void listeners.delete(onChange);
}

/** Changes whenever the claims do (a snapshot for `useSyncExternalStore`). */
export function loadClaimsVersion(): number {
  return version;
}
