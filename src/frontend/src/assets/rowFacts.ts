// What ONE row of the Assets tab says, as data.
//
// The row renderer receives the view and an id and nothing else, and this is
// the function it calls: every badge, dim, gap, stale mark and drift flag is a
// pure function of one `AssetView`. That is the one-derived-view property the
// note makes a verification gate -- a row that computed anything for itself
// could disagree with its neighbour, and the tree would stop being consistent.

import type { AssetView } from "./assetView";
import { ROLE_CONTENT } from "./assetView";
import type { HierarchyDrift, NodeFreshness } from "./freshness";
import { ancestorsOf, type Hierarchy } from "./hierarchy";
import type { AssetNode, DeliveryKind } from "./types";

export type BadgeWeight = "solid" | "ghost" | "below";

export interface RowBadge {
  /** Where the published content stands to this row: rooted HERE, covering
   *  from ABOVE, or rooted BENEATH. */
  readonly weight: BadgeWeight;
  /** The delivery claim of the publish the badge is about -- the letter shown. */
  readonly delivery: DeliveryKind;
  /** The node that publish is rooted at (this row for `solid`). */
  readonly at: string;
  /** The revision it resolved to. */
  readonly revision: string;
}

export interface RowFacts {
  readonly node: AssetNode;
  readonly badge: RowBadge | null;
  /** Nothing to deliver at or below this row. Rendered, never hidden. */
  readonly dimmed: boolean;
  /** Something at or below, and no publish at or above covers it. */
  readonly gap: boolean;
  /** Leaves at or below -- the count a collapsed branch shows. */
  readonly payload: number;
  /** Of those, how many no publish covers -- what `gap` is about. */
  readonly uncovered: number;
  readonly freshness: NodeFreshness | null;
  /** This row's own subject was published against an older tree. */
  readonly drift: HierarchyDrift | null;
  /** The revision this row's own subject resolved to, if it is one. */
  readonly resolvedRevision: string | null;
}

function deliveryOf(view: AssetView, subject: string): { delivery: DeliveryKind; revision: string } | null {
  const content = view.resolution.subjects.get(subject)?.content;
  if (!content) return null;
  return { delivery: content.manifest?.delivery ?? "none", revision: content.revision };
}

export function rowFacts(view: AssetView, id: string): RowFacts | null {
  const hnode = view.hierarchy.byId.get(id);
  if (!hnode) return null;
  const cov = view.coverage.byId.get(id);

  let badge: RowBadge | null = null;
  if (cov?.rooted.has(ROLE_CONTENT)) {
    const d = deliveryOf(view, id);
    if (d) badge = { weight: "solid", at: id, ...d };
  } else if (cov?.covered.has(ROLE_CONTENT)) {
    const owner = cov.coveredBy.get(ROLE_CONTENT)!;
    const d = deliveryOf(view, owner);
    if (d) badge = { weight: "ghost", at: owner, ...d };
  } else if (cov?.below.has(ROLE_CONTENT)) {
    const owner = cov.belowBy.get(ROLE_CONTENT)!;
    const d = deliveryOf(view, owner);
    if (d) badge = { weight: "below", at: owner, ...d };
  }

  return {
    node: hnode.data,
    badge,
    dimmed: (cov?.dimmed ?? false) && !view.unexplored.has(id),
    gap: cov?.gap ?? false,
    payload: cov?.payloadSubtree ?? 0,
    uncovered: cov?.uncoveredSubtree ?? 0,
    freshness: view.freshness.get(id) ?? null,
    drift: view.drift.get(id) ?? null,
    resolvedRevision: view.resolution.subjects.get(id)?.revision.revision ?? null,
  };
}

/** The rows a search keeps: every match and every ancestor of one, so the set
 *  is ancestor-closed as `flattenVisible`'s `include` requires. Also returns the
 *  ancestors, which the tab treats as expanded while the search is active --
 *  a hit inside a collapsed branch is otherwise found and not shown. */
export function searchRows(
  h: Hierarchy<AssetNode>,
  term: string,
): { include: ReadonlySet<string>; open: ReadonlySet<string>; matches: number } | null {
  const q = term.trim().toLowerCase();
  if (!q) return null;
  const include = new Set<string>();
  const open = new Set<string>();
  let matches = 0;
  for (const [id, node] of h.byId) {
    const d = node.data;
    if (!(d.label.toLowerCase().includes(q) || d.id.toLowerCase().includes(q))) continue;
    matches++;
    include.add(id);
    for (const a of ancestorsOf(h, id)) {
      if (include.has(a) && open.has(a)) break; // the rest of the chain is already in
      include.add(a);
      open.add(a);
    }
  }
  return { include, open, matches };
}
