// Two "is this row out of date" facts that live in the asset store itself, and
// must stay two facts. (The third -- "the upstream source moved since this was
// published" -- is the change feed's, a different input, and never folded into
// either of these.)
//
// 1. STALE: the SHAPE of the tree on screen no longer matches the resolution its
//    badges are read from.
//
//    The forest is a UNION: a merge adds rows and deletes only inside a subtree
//    document that claims completeness (./merge). So a row can outlive the
//    publish that produced it -- after a mode switch (which deliberately does
//    not re-collapse the tree or drop spines already held), or after a node was
//    removed at the source and absence could not delete it. Marking is the
//    honest move and dropping is not: this module says which revision a row was
//    drawn from and whether the resolution has moved on, and leaves the row on
//    screen. Refresh rebuilds from nothing.
//
//    By SUBJECT, not by walking ancestors: each row records the spine that
//    contributed it, so the question is O(1) per row.
//
// 2. DRIFT: a subject was PUBLISHED AGAINST an older collection hierarchy than
//    the one the tree is drawn from. A leaf publish records, in its manifest,
//    the `hierarchy_revision` it was derived against; when the tree on screen
//    comes from a newer collection index, the leaf's placement was decided on a
//    tree that has since changed, and the row says so rather than guessing
//    whether it matters.

/** Where a row in the forest came from. Recorded at merge time. */
export interface NodeOrigin {
  /** The subject whose `hierarchy.json` contributed this row. */
  readonly subject: string;
  /** The revision that spine was fetched at. */
  readonly revision: string;
}

export interface NodeFreshness {
  /** The revision of the spine this row is actually drawn from. */
  readonly shownAt: string;
  /** What that subject resolves to NOW, or null when the mode resolves it to
   *  nothing (normal under `run` / `as-of`, and NOT staleness). */
  readonly resolvedAt: string | null;
  readonly stale: boolean;
}

/** Per-row freshness.
 *
 * `revisionOf` is asked per ORIGIN (subject + the revision it was fetched at),
 * not per subject, because one subject can legitimately contribute rows at
 * several revisions: the collection index is a UNION of every index the mode
 * admits (./assetIndex `collectionIndexRevisions`), so a row from an older index
 * the mode still includes is current, not stale. Memoised per origin: one spine
 * is up to ~41k rows against a single origin. */
export function nodeFreshness(
  origins: ReadonlyMap<string, NodeOrigin>,
  revisionOf: (origin: NodeOrigin) => string | null,
): Map<string, NodeFreshness> {
  const out = new Map<string, NodeFreshness>();
  // Two memo levels. By object first: every row one merge contributed carries
  // the SAME origin object, so a 41k-row spine is one lookup and one shared
  // result rather than 41k string keys and 41k objects. By value second, for
  // origins that are equal without being identical.
  const byObject = new Map<NodeOrigin, NodeFreshness>();
  const byValue = new Map<string, NodeFreshness>();
  for (const [id, origin] of origins) {
    let f = byObject.get(origin);
    if (f === undefined) {
      const key = `${origin.subject}@${origin.revision}`;
      f = byValue.get(key);
      if (f === undefined) {
        const resolvedAt = revisionOf(origin);
        f = Object.freeze({
          shownAt: origin.revision,
          resolvedAt,
          stale: resolvedAt !== null && resolvedAt !== origin.revision,
        });
        byValue.set(key, f);
      }
      byObject.set(origin, f);
    }
    out.set(id, f);
  }
  return out;
}

/** How many rows are drawn from a superseded spine. The banner counts these,
 *  because a stale row inside a collapsed branch is otherwise invisible. */
export function staleCount(freshness: ReadonlyMap<string, NodeFreshness>): number {
  let n = 0;
  for (const f of freshness.values()) if (f.stale) n++;
  return n;
}

export interface HierarchyDrift {
  /** The collection hierarchy the subject's manifest says it was derived against. */
  readonly publishedAgainst: string;
  /** The collection hierarchy the tree is drawn from now. */
  readonly shownFrom: string;
}

/** Drift for one subject, or null when there is none to report.
 *
 * Null -- not "fine" -- when the manifest recorded no `hierarchy_revision`
 * (most whole-collection publishes derive the index themselves and have nothing
 * to point back to) or when no collection index is on screen. Only a strictly
 * OLDER recorded revision is drift: equal is the normal case, and newer means
 * the subject is ahead of the tree, which orphans/`ahead` already says. */
export function hierarchyDrift(
  hierarchyRevision: string | null,
  spineRevision: string | null,
): HierarchyDrift | null {
  if (!hierarchyRevision || !spineRevision) return null;
  return hierarchyRevision < spineRevision
    ? { publishedAgainst: hierarchyRevision, shownFrom: spineRevision }
    : null;
}
