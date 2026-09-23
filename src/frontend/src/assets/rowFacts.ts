// What ONE row of the Assets tab says, as data.
//
// The row renderer receives the view and an id and nothing else, and this is
// the function it calls: every badge, dim, gap, stale mark and drift flag is a
// pure function of one `AssetView`. That is the one-derived-view property the
// note makes a verification gate -- a row that computed anything for itself
// could disagree with its neighbour, and the tree would stop being consistent.

import type { AssetView } from "./assetView";
import { ROLE_CONTENT } from "./assetView";
import type { ChangeAction, ChangeState } from "./changes";
import type { HierarchyDrift, NodeFreshness } from "./freshness";
import { ancestorsOf, type Hierarchy } from "./hierarchy";
import type { AssetNode, ChangeRecord, DeliveryKind } from "./types";

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
  /** Behind-upstream, ONLY when this row is itself an export root the feed
   *  has been asked about (`view.changes.byRoot`). `null` for every other
   *  row -- including an unasked root -- never a guessed `not-recorded`.
   *  A DIFFERENT fact from `freshness` above; see `./changes`'s module
   *  comment for why the two must never share a mark. */
  readonly changeState: ChangeState | null;
  /** What the sweep found AT this node (`added`/`modified`/`deleted`),
   *  independent of `changeState` -- a node deep under a `behind` root that
   *  the sweep did not itself touch has no mark here, only its root does. */
  readonly evidenceMark: ChangeAction | null;
  /** This row's own subject's `change` record (§Decision 6), when its
   *  resolved manifest carries one. `null` is the normal case. */
  readonly changeRecord: ChangeRecord | null;
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
    changeState: view.changes.byRoot.get(id)?.state ?? null,
    evidenceMark: view.evidenceMarks.get(id) ?? null,
    changeRecord: view.resolution.subjects.get(id)?.revision.manifest?.change ?? null,
  };
}

export interface ChangeOwner {
  readonly id: string;
  readonly display: string | null;
}

/** Distinct actors across every resolved subject's `change` record in this
 *  view -- both `publishedBy` (core-verified) and `sourceActor` (merely
 *  relayed) -- deduped by id. What the "changed by" filter offers; empty
 *  exactly when `view.hasChangeOwners` is false, which is the gate that
 *  decides whether the filter is shown at all: §Decision 6 says a manifest
 *  with no actor is the NORMAL case, and a filter over zero owners is worse
 *  than no filter -- it invites a click that can only ever find nothing. */
export function changeOwners(view: AssetView): readonly ChangeOwner[] {
  const seen = new Map<string, ChangeOwner>();
  for (const resolved of view.resolution.subjects.values()) {
    const c = resolved.revision.manifest?.change;
    if (!c) continue;
    for (const a of [c.publishedBy, c.sourceActor]) {
      if (a && !seen.has(a.id)) seen.set(a.id, { id: a.id, display: a.display });
    }
  }
  return [...seen.values()].sort((a, b) => a.id.localeCompare(b.id));
}

/** Every subject whose OWN `change` record names `actorId`, as either the
 *  verified publisher or the relayed source actor -- what the filter narrows
 *  the tree to once an owner is chosen. */
export function subjectsByOwner(view: AssetView, actorId: string): readonly string[] {
  const out: string[] = [];
  for (const [subject, resolved] of view.resolution.subjects) {
    const c = resolved.revision.manifest?.change;
    if (c && (c.publishedBy?.id === actorId || c.sourceActor?.id === actorId)) out.push(subject);
  }
  return out;
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
