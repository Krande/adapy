// A provider change-check run, as the Sources tab shows it: the run picker's line, the row
// list's filter, and the marks a run puts on the tree.
//
// Pure, like `./changes`: React-free and request-free, so the rules are testable without a DOM.
//
// RUN MARKS ARE NOT THE CHANGE FEED. `./changes` reads the feed -- each node's LATEST recorded
// change, cumulative across sweeps -- and `evidenceMarks` draws it. A run is one check: what THAT
// check found. Picking a run replaces the tree's evidence marks with the run's for as long as it
// is picked, so "what did Tuesday's check flag?" is answered by Tuesday's check alone.

import type { ChangeAction } from "./changes";

/** The item shape (`ada.assets/change-items@1`), structurally -- see `services/api/assetSchedules`. */
export interface RunItem {
  readonly node: string | null;
  readonly action: string;
  readonly changed_at: string | null;
  readonly changed_by: string | null;
  readonly name: string | null;
  readonly detail: string | null;
}

/** The run fields the picker reads. */
export interface RunSummary {
  readonly status: string;
  readonly created_at: string | null;
  readonly requested_via: string;
  readonly stale: boolean | null;
  readonly up_to_date: boolean | null;
  readonly counts: Readonly<Record<string, number | undefined>> | null;
  readonly users: readonly string[];
  readonly message: string | null;
  readonly error: string | null;
}

/** The tree's three-mark vocabulary. A provider's `changed` (modified, kind unknown) and
 *  `unknown` both draw as modified: the row DID move, and "how" is in the row list's detail. */
export function treeAction(action: string): ChangeAction {
  return action === "added" ? "added" : action === "deleted" ? "deleted" : "modified";
}

/** node id -> mark for one run. An item with no node (not published here yet) marks nothing;
 *  when a node appears twice the strongest action wins (deleted > added > modified). */
export function runMarks(items: readonly RunItem[]): ReadonlyMap<string, ChangeAction> {
  const rank: Record<ChangeAction, number> = { deleted: 3, added: 2, modified: 1 };
  const out = new Map<string, ChangeAction>();
  for (const item of items) {
    if (!item.node) continue;
    const mark = treeAction(item.action);
    const have = out.get(item.node);
    if (!have || rank[mark] > rank[have]) out.set(item.node, mark);
  }
  return out;
}

export interface ItemFilter {
  readonly user?: string;
  readonly action?: string;
  readonly query?: string;
}

/** Rows matching the filter: an exact user and action (each optional), and a case-insensitive
 *  substring over the name, node id and detail. */
export function filterRunItems(items: readonly RunItem[], filter: ItemFilter): RunItem[] {
  const q = (filter.query ?? "").trim().toLowerCase();
  return items.filter(
    (i) =>
      (!filter.user || i.changed_by === filter.user) &&
      (!filter.action || treeAction(i.action) === filter.action) &&
      (!q || [i.name, i.node, i.detail].some((v) => v != null && v.toLowerCase().includes(q))),
  );
}

/** How many items each user is behind, most first -- the row list's user filter. */
export function itemsByUser(items: readonly RunItem[]): [string, number][] {
  const counts = new Map<string, number>();
  for (const i of items) if (i.changed_by) counts.set(i.changed_by, (counts.get(i.changed_by) ?? 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
}

/** The changed-node count a run reports (added + modified + deleted + unknown). */
export function changedCount(run: Pick<RunSummary, "counts">): number | null {
  if (!run.counts) return null;
  return Object.values(run.counts).reduce<number>((n, v) => n + (typeof v === "number" ? v : 0), 0);
}

/** The run picker's one line: when, how it was started, and what it found. */
export function runLabel(run: RunSummary, formatTime: (iso: string | null) => string): string {
  const when = formatTime(run.created_at);
  const how = run.requested_via === "schedule" ? "scheduled" : "manual";
  let what: string;
  if (run.status === "queued") what = "running…";
  else if (run.status === "error") what = "failed";
  else if (run.up_to_date) what = "up to date";
  else {
    const n = changedCount(run);
    const users = run.users.length;
    what =
      n !== null
        ? `${n} changed${users ? ` by ${users} user${users === 1 ? "" : "s"}` : ""}`
        : run.stale
          ? "changed"
          : "could not tell";
  }
  return `${when} · ${what} · ${how}`;
}
