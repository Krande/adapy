// ONE resolution context for the whole tree, not a max() per row.
//
// WHY THIS IS A MODULE AND NOT AN EXPRESSION. The obvious implementation is
// `max(revision)` computed wherever a row needs it. That produces a tree that
// disagrees with itself: the badge on a parent can come from one publish and the
// badge on its child from another, a Load can fetch a third, and no screenshot
// of the result is reproducible. So resolution is lifted out into a context
// object computed once per (index, collection, mode), and EVERY badge reads that
// one object. If the modes below are ever extended, the extension goes here and
// the tree cannot fall out of step.
//
// The three modes are not three flavours of the same thing:
//
//   latest    per subject, max(revision).            Browsing. NOT coeval.
//   as-of X   per subject, max(revision <= X).       Reproducible. NOT coeval.
//   run T     per subject, revision == T exactly.    The only coeval mode.
//
// "Coeval" means every subject in the resolution came out of the same publish.
// Only `run` guarantees it, and it is free: a fan-out shares one revision stamp,
// so asking for one stamp returns a set that was genuinely produced together.
// `latest` is the default because browsing is the common case, but the tab must
// SAY when it is mixing -- see `describeResolution`.
//
// Only COMPLETE revisions (a manifest exists) are candidates, in every mode.
// Manifests are written last, so a manifest-less revision is a publish that died
// partway; the server's tree and delivery routes skip it, and a browser that
// resolved to it would badge a row the server then refuses to serve.

import { compareRevisions, isComplete, subjectsOf } from "./assetIndex";
import type { AssetIndex, AssetRevision, AssetSubject, ResolutionMode } from "./types";

export type { ResolutionMode } from "./types";

/** What one subject resolved to under the active mode. */
export interface ResolvedSubject {
  readonly subject: string;
  readonly revision: AssetRevision;
  /** The subject's newest complete revision overall, whatever the mode picked --
   *  so an absence can be stated with a concrete alternative. */
  readonly newest: string;
  /** The newest revision satisfying the mode that also CARRIES CONTENT, or null.
   *
   * WHY A SECOND PICK EXISTS, given this module's whole argument is one
   * resolution rather than a `max()` per row: a revision can mention a subject
   * without containing anything for it. A hierarchy-only publish writes a
   * manifest at every root it declares and no delivery claim anywhere; under a
   * single pick it -- being newest -- becomes every subject's answer, and an
   * earlier publish that DOES carry content is shadowed. It is still ONE
   * resolution (same mode, same bound, computed once) answering two questions
   * that were always different. Equal to `revision` when no predicate is given. */
  readonly content: AssetRevision | null;
}

export interface ResolveOptions {
  /** Whether a revision carries content, as opposed to merely naming subjects.
   *  Omitted, every revision counts and `content` collapses onto `revision`. */
  readonly carriesContent?: (revision: AssetRevision) => boolean;
}

export interface Resolution {
  readonly collection: string;
  readonly mode: ResolutionMode;
  /** subject -> what it resolved to. A subject nothing satisfies is ABSENT here
   *  and recorded in `missing` instead. */
  readonly subjects: ReadonlyMap<string, ResolvedSubject>;
  readonly missing: ReadonlyMap<string, { readonly subject: string; readonly newest: string }>;
  /** Subjects whose newest revision has no manifest: a publish that died
   *  partway. Counted so the tab can say so rather than quietly resolving past it. */
  readonly incomplete: readonly string[];
}

const ANY = (): boolean => true;

/** The revision the mode selects among those `accept` allows. `accept` narrows
 *  the CANDIDATES, never the rule -- so under `run` the content pick is that
 *  run's own revision or null, which keeps the mode coeval. */
function pick(
  subject: AssetSubject,
  mode: ResolutionMode,
  accept: (revision: AssetRevision) => boolean = ANY,
): AssetRevision | null {
  const revs = subject.revisions; // ascending
  switch (mode.kind) {
    case "latest": {
      for (let i = revs.length - 1; i >= 0; i--) if (isComplete(revs[i]) && accept(revs[i])) return revs[i];
      return null;
    }
    case "as-of": {
      for (let i = revs.length - 1; i >= 0; i--) {
        const r = revs[i];
        if (compareRevisions(r.revision, mode.revision) <= 0 && isComplete(r) && accept(r)) return r;
      }
      return null;
    }
    case "run": {
      const hit = revs.find((r) => r.revision === mode.revision) ?? null;
      return hit && isComplete(hit) && accept(hit) ? hit : null;
    }
  }
}

export function resolveCollection(
  index: AssetIndex,
  collection: string,
  mode: ResolutionMode,
  options: ResolveOptions = {},
): Resolution {
  const subjects = new Map<string, ResolvedSubject>();
  const missing = new Map<string, { subject: string; newest: string }>();
  const incomplete: string[] = [];
  const carries = options.carriesContent;
  for (const [name, subject] of subjectsOf(index, collection)) {
    const revs = subject.revisions;
    if (revs.length && !isComplete(revs[revs.length - 1])) incomplete.push(name);
    let newest = "";
    for (let i = revs.length - 1; i >= 0; i--) {
      if (isComplete(revs[i])) {
        newest = revs[i].revision;
        break;
      }
    }
    const revision = pick(subject, mode);
    if (revision) {
      const content = carries ? pick(subject, mode, carries) : revision;
      subjects.set(name, { subject: name, revision, newest, content });
    } else if (newest) missing.set(name, { subject: name, newest });
  }
  incomplete.sort();
  return { collection, mode, subjects, missing, incomplete };
}

/** The distinct revisions actually in play, ascending. */
export function revisionsInPlay(resolution: Resolution, skip?: (subject: string) => boolean): string[] {
  const seen = new Set<string>();
  for (const s of resolution.subjects.values()) {
    if (skip?.(s.subject)) continue;
    seen.add(s.revision.revision);
  }
  return [...seen].sort(compareRevisions);
}

/** What the indicator beside the mode control says. `mixed` is the load-bearing
 *  field: a `latest` resolution spanning three publish dates is not wrong, but a
 *  user reading badges off it is comparing things never produced together, and
 *  that has to be on screen rather than inferable. `run` is coeval by
 *  construction, so `mixed` is false there whatever the data looks like. */
export interface ResolutionSummary {
  readonly mixed: boolean;
  readonly coeval: boolean;
  readonly revisions: readonly string[];
  readonly subjectCount: number;
  readonly missingCount: number;
  readonly incompleteCount: number;
}

export function describeResolution(
  resolution: Resolution,
  skip?: (subject: string) => boolean,
): ResolutionSummary {
  const revisions = revisionsInPlay(resolution, skip);
  const coeval = resolution.mode.kind === "run";
  return {
    mixed: !coeval && revisions.length > 1,
    coeval,
    revisions,
    subjectCount: resolution.subjects.size,
    missingCount: resolution.missing.size,
    incompleteCount: resolution.incomplete.length,
  };
}
