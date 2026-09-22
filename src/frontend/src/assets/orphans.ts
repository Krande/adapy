// Why a published subject has no row, and where its node used to sit.
//
// An orphan is a published subject whose node matches no row in any loaded
// spine. It is NOT dropped: the subject is real and readable, and dropping it
// makes a reachable asset invisible. But "there is an entry you cannot see" is
// only half an answer; the other half is WHY, and there are two causes that are
// opposites:
//
//   removed   the subject is OLDER than the hierarchy being shown. A newer
//             hierarchy no longer contains the node; the publish outlived it.
//   ahead     the subject is NEWER than the hierarchy being shown. Nothing in
//             this hierarchy places the node yet; the node outran the tree.
//
// Which applies is decidable from revisions already held -- no feed, no
// publisher record -- so this is a small pure function.

import type { Hierarchy } from "./hierarchy";

export type OrphanCause = "removed" | "ahead";

export interface OrphanEntry {
  /** The node id the subject is published against. */
  readonly id: string;
  /** The revision the subject itself resolved to. */
  readonly revision: string;
  /** The revision of the hierarchy it is being judged against. */
  readonly spineRevision: string;
  readonly cause: OrphanCause;
  /** Labels of the ancestors still present in the forest, OUTERMOST FIRST, or
   *  empty when none are loaded. */
  readonly path: readonly string[];
}

/** Equal revisions cannot be "the tree moved" -- the subject and the hierarchy
 *  are one publish -- so that case reads `ahead`, whose sentence ("nothing in
 *  this hierarchy places it") is the true one there too. */
export function orphanCause(entryRevision: string, spineRevision: string): OrphanCause {
  return entryRevision < spineRevision ? "removed" : "ahead";
}

/** The loaded ancestors of a node that is NOT itself in the forest, found from
 *  a parent it declared somewhere (a manifest's node, a previous spine). A node
 *  with no recoverable parent reports an empty path rather than a guess. */
export function lastKnownPath<T>(
  hierarchy: Hierarchy<T>,
  id: string,
  parentOf: (id: string) => string | null,
  labelOf: (data: T) => string,
): readonly string[] {
  const out: string[] = [];
  const seen = new Set<string>([id]);
  let cursor = parentOf(id);
  while (cursor !== null && !seen.has(cursor) && out.length < 32) {
    seen.add(cursor);
    const node = hierarchy.byId.get(cursor);
    if (!node) break;
    out.push(labelOf(node.data));
    cursor = node.parent;
  }
  return out.reverse();
}

/** The sentence for an orphan. Both revisions are IN the sentence rather than
 *  in a tooltip: a `title` is unreachable on touch, and the list is short. */
export function orphanSentence(o: OrphanEntry, revLabel: (rev: string) => string): string {
  if (o.cause === "removed") {
    return (
      `Removed by a newer hierarchy. The ${revLabel(o.spineRevision)} tree no longer contains ` +
      `this node, but its publish from ${revLabel(o.revision)} is still there.`
    );
  }
  return (
    `Published after this hierarchy. Nothing in the ${revLabel(o.spineRevision)} tree places ` +
    `this node; a newer hierarchy publish would show where it sits.`
  );
}

export function orphanHeading(cause: OrphanCause): string {
  return cause === "removed" ? "Removed by a newer hierarchy" : "Published after this hierarchy";
}
