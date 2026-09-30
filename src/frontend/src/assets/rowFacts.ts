// What ONE row of the Assets tab says, as data.
//
// The row renderer receives the view and an id and nothing else, and this is
// the function it calls: every badge, dim, gap, stale mark and drift flag is a
// pure function of one `AssetView`. That is the one-derived-view property the
// note makes a verification gate -- a row that computed anything for itself
// could disagree with its neighbour, and the tree would stop being consistent.

import type { AssetView } from "./assetView";
import { ROLE_CONTENT, contentRole } from "./assetView";
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
  /** The provider whose manifest makes the claim -- what a load asks for. Read off that
   *  manifest, not off the row: a node's row names whichever provider's spine merged last. */
  readonly provider: string;
}

export interface RowFacts {
  readonly node: AssetNode;
  readonly badge: RowBadge | null;
  /** One badge per provider with content at, above or below this row, in `view.contentProviders`
   *  order. `badge` is the any-provider answer (the nearest publish); these are what a row offers
   *  when a node carries geometry from more than one provider. */
  readonly claims: readonly RowBadge[];
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

function deliveryOf(
  view: AssetView,
  subject: string,
  provider?: string,
): { delivery: DeliveryKind; revision: string; provider: string } | null {
  const resolved = view.resolution.subjects.get(subject);
  const content = provider === undefined ? resolved?.content : resolved?.byProvider.get(provider);
  if (!content) return null;
  return {
    delivery: content.manifest?.delivery ?? "none",
    revision: content.revision,
    provider: content.manifest?.provider ?? provider ?? "",
  };
}

/** The badge for one coverage role at `id`: rooted here, covering from above, or rooted below. */
function badgeFor(view: AssetView, id: string, role: string, provider?: string): RowBadge | null {
  const cov = view.coverage.byId.get(id);
  if (cov?.rooted.has(role)) {
    const d = deliveryOf(view, id, provider);
    return d ? { weight: "solid", at: id, ...d } : null;
  }
  if (cov?.covered.has(role)) {
    const owner = cov.coveredBy.get(role)!;
    const d = deliveryOf(view, owner, provider);
    return d ? { weight: "ghost", at: owner, ...d } : null;
  }
  if (cov?.below.has(role)) {
    const owner = cov.belowBy.get(role)!;
    const d = deliveryOf(view, owner, provider);
    return d ? { weight: "below", at: owner, ...d } : null;
  }
  return null;
}

export function rowFacts(view: AssetView, id: string): RowFacts | null {
  const hnode = view.hierarchy.byId.get(id);
  if (!hnode) return null;
  const cov = view.coverage.byId.get(id);

  const badge = badgeFor(view, id, ROLE_CONTENT);
  const claims: RowBadge[] = [];
  for (const p of view.contentProviders) {
    const b = badgeFor(view, id, contentRole(p), p);
    if (b) claims.push(b);
  }

  return {
    node: hnode.data,
    badge,
    claims,
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

/** What a search compares, per hierarchy, built once: a whole project is most of a million
 *  rows, and lowercasing two strings per row on every keystroke was the search's own cost. `last`
 *  is the previous query's hits -- a term typed further can only match a subset of them. */
interface SearchIndex {
  readonly ids: readonly string[];
  /** `label NUL id`, lowercased. A typed term holds no NUL, so it matches one field or the other,
   *  never across the two. */
  readonly keys: readonly string[];
  last: { q: string; hits: readonly number[] } | null;
}

const searchIndexes = new WeakMap<Hierarchy<AssetNode>, SearchIndex>();

/** Shorter terms are not searched. One character matches nearly every row of a project -- all of
 *  them, for a common letter -- which filters nothing and costs most of a second on 650k rows to
 *  say so. Two is already selective: 670 hits for "st" in that tree. */
export const MIN_SEARCH_CHARS = 2;

/** Whether `term` is long enough to search. One test for the tree and the tab, so the tab's
 *  search mode and the tree's filter never disagree about whether a search is on. */
export function isSearchTerm(term: string): boolean {
  return term.trim().length >= MIN_SEARCH_CHARS;
}

function matchingIds(h: Hierarchy<AssetNode>, q: string): string[] {
  let ix = searchIndexes.get(h);
  if (!ix) {
    const ids: string[] = [];
    const keys: string[] = [];
    for (const [id, node] of h.byId) {
      ids.push(id);
      keys.push(`${node.data.label}\u0000${node.data.id}`.toLowerCase());
    }
    ix = { ids, keys, last: null };
    searchIndexes.set(h, ix);
  }
  const hits: number[] = [];
  if (ix.last && q.includes(ix.last.q)) {
    for (const i of ix.last.hits) if (ix.keys[i].includes(q)) hits.push(i);
  } else {
    for (let i = 0; i < ix.keys.length; i++) if (ix.keys[i].includes(q)) hits.push(i);
  }
  ix.last = { q, hits };
  return hits.map((i) => ix!.ids[i]);
}

/** The rows a search keeps: every match and every ancestor of one, so the set
 *  is ancestor-closed as `flattenVisible`'s `include` requires. Case-insensitive,
 *  on the label and the id.
 *
 *  Also returns the ancestors to treat as expanded while the search is active --
 *  a hit inside a collapsed branch is otherwise found and not shown -- EXCEPT an
 *  ancestor that is itself a hit. That row is the answer at its level, and
 *  opening it for the deeper hits below would push the next hit at its level off
 *  the screen; they are one expansion away, and still the only rows under it.
 *
 *  `hits` is the matched ids, for ranking branches by their shallowest hit. */
export function searchRows(
  h: Hierarchy<AssetNode>,
  term: string,
): { include: ReadonlySet<string>; open: ReadonlySet<string>; hits: ReadonlySet<string>; matches: number } | null {
  if (!isSearchTerm(term)) return null;
  const q = term.trim().toLowerCase();
  const include = new Set<string>();
  const open = new Set<string>();
  const hits = new Set<string>();
  for (const id of matchingIds(h, q)) {
    hits.add(id);
    include.add(id);
    for (const a of ancestorsOf(h, id)) {
      if (include.has(a) && open.has(a)) break; // the rest of the chain is already in
      include.add(a);
      open.add(a);
    }
  }
  for (const id of hits) open.delete(id);
  return { include, open, hits, matches: hits.size };
}

/** Per row of `h`, the depth of the shallowest search hit at or below it; absent for a row with
 *  none. Ranking siblings by it puts a hit at their own level first, then the branches whose hits
 *  are one level down, and so on. Pass the hierarchy as DRAWN, so a row's branch is the one the
 *  user sees after any flattened kinds. */
export function shallowestHit<T>(h: Hierarchy<T>, hits: ReadonlySet<string>): ReadonlyMap<string, number> {
  const best = new Map<string, number>();
  for (const id of hits) {
    const d = h.byId.get(id)?.depth;
    if (d === undefined) continue;
    for (let cur: string | null = id; cur !== null; cur = h.byId.get(cur)?.parent ?? null) {
      if ((best.get(cur) ?? Infinity) <= d) break; // everything above already ranks at least this well
      best.set(cur, d);
    }
  }
  return best;
}
