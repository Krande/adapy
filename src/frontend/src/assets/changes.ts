// Has the SOURCE moved since we published it? A different question from
// `./freshness`, answered from a different input, and drawn with a different
// mark -- see the module comment there for the boundary. This module reads
// `GET /scopes/{scope}/source-nodes`, a change feed a provider's sweep writes
// (never this module, never the browser: writing is the sweep's job, through
// the worker facade or the POST route the GET sits beside).
//
// TWO SUBJECTS, NOT ONE. An export root is the UNIT OF WORK -- it is what a
// re-export re-does -- and a changed node is the UNIT OF EVIDENCE. A reader
// told "this root is behind" still needs to know WHAT moved under it, so both
// are modelled: `classifyChanges` answers the first, `evidenceMarks` the
// second, and neither is derived from the other.
//
// FOUR STATES, AND THE PAIR THAT MUST NEVER COLLAPSE. `current` and
// `not-recorded` look identical to a careless renderer -- both mean "no bad
// news" -- and are opposite in what they claim. A ref ABSENT from an answer
// the feed actually gave means NOBODY LOOKED: the sweep never covered that
// root, and rendering it as `current` turns "we have no idea" into "you are
// fine", the one mistake a change feed exists to prevent. `no-feed` is a
// third, separate "cannot say": the deployment has no database at all
// (`_source_nodes_pool`, 503) and answered NOTHING, so nothing this module
// classifies from `no-feed` may be `current` either -- see `classifyChanges`.
//
// WHY THIS MODULE DOES NOT COMPARE TIMESTAMPS BLINDLY. A row present in the
// answer means the feed recorded something for that ref -- an actual change,
// or the roll-up mark an ancestor gets when something changed beneath it
// (§Decision 4). Its `last_changed_at` is compared against the export root's
// own resolved REVISION, in the compact-UTC revision domain (`./keys`) rather
// than as two independently-parsed Date objects: the revision token's whole
// contract is that lexical order is chronological order, and converting the
// feed's ISO instant into that same domain once, here, keeps every comparison
// in this module reading off the one ordering the rest of the asset store
// already trusts, instead of re-deriving it per call site.

import { compareRevisions } from "./assetIndex";
import { revisionFromInstant } from "./keys";
import type { WireSourceNodeRow, WireSourceNodesRefsResponse } from "./types";

/** A node the sweep found added, modified or deleted. NOCHANGE is never a
 *  value the feed stores (§Decision 7): a row's absence already means "no
 *  change was recorded here", and writing a `nochange` row for every
 *  untouched node would turn the feed into a full mirror of the source. */
export type ChangeAction = "added" | "modified" | "deleted";

export interface SourceNodeRow {
  readonly nodeRef: string;
  readonly parentRef: string | null;
  readonly name: string | null;
  readonly lastChangedAt: string;
  readonly lastChangedBy: string | null;
  readonly observedAt: string;
  readonly action: ChangeAction | null;
}

/** One `GET .../source-nodes?source=&refs=...` reply, parsed. `source` is the
 *  provider id the refs were asked under -- a mixed collection asks more than
 *  one, and each source's rows (and each source's own no-feed-ness) are kept
 *  apart by the caller (`assetBrowserStore.sourceAnswer` is keyed by it). */
export interface SourceNodesAnswer {
  readonly source: string;
  readonly rows: ReadonlyMap<string, SourceNodeRow>;
  readonly unknown: ReadonlySet<string>;
}

export function sourceNodesAnswerFromWire(wire: WireSourceNodesRefsResponse): SourceNodesAnswer {
  const rows = new Map<string, SourceNodeRow>();
  for (const n of wire.nodes) rows.set(n.node_ref, sourceNodeRowFromWire(n));
  return { source: wire.source, rows, unknown: new Set(wire.unknown) };
}

function sourceNodeRowFromWire(n: WireSourceNodeRow): SourceNodeRow {
  return {
    nodeRef: n.node_ref,
    parentRef: n.parent_ref ?? null,
    name: n.name ?? null,
    lastChangedAt: n.last_changed_at,
    lastChangedBy: n.last_changed_by ?? null,
    observedAt: n.observed_at,
    action: n.action ?? null,
  };
}

// ---------------------------------------------------------------------------
// root state: behind | current | not-recorded | no-feed
// ---------------------------------------------------------------------------

export type ChangeState = "behind" | "current" | "not-recorded" | "no-feed";

/** One export root: a published subject and the revision it is resolved to
 *  right now (`resolution.subjects`, not the newest revision overall -- the
 *  question is "is THIS publish behind", not "is the newest one"). */
export interface ExportRoot {
  readonly subject: string;
  readonly revision: string;
}

export interface RootChange {
  readonly subject: string;
  readonly state: ChangeState;
  /** The revision this state was judged against. */
  readonly revision: string;
  readonly lastChangedAt: string | null;
  readonly lastChangedBy: string | null;
  /** What the feed's own row said happened, when it has an opinion (a pure
   *  roll-up ancestor row carries none, only the changed node itself does). */
  readonly action: ChangeAction | null;
}

export interface ChangeReport {
  /** subject -> its state. A subject ABSENT here was never asked about --
   *  see `classifyChanges`'s `asked` parameter -- which is a third thing
   *  again, distinct from all four states: "not asked" is not "not-recorded",
   *  because "not-recorded" is a claim about the FEED's coverage and "not
   *  asked" is a claim about the BROWSER's, and only the browser's own
   *  `evidenceAsked` set may tell that one apart from real ignorance. */
  readonly byRoot: ReadonlyMap<string, RootChange>;
  readonly behind: number;
  readonly current: number;
  readonly notRecorded: number;
}

const EMPTY_REPORT: ChangeReport = Object.freeze({
  byRoot: new Map(),
  behind: 0,
  current: 0,
  notRecorded: 0,
});

/** What a caller (`./assetView`) knows about one root: whether the browser
 *  ever asked the feed about it, and if so, the answer it got back for the
 *  PROVIDER that root belongs to. `answer: null` is that provider's own
 *  no-feed (its source has no database, or no source at all in this scope);
 *  it is looked up per call rather than passed as one flat answer because a
 *  mixed collection can straddle providers with different feed availability,
 *  and this module has no notion of "provider" of its own to key by. */
export type RootLookup = (subject: string) => { readonly asked: boolean; readonly answer: SourceNodesAnswer | null };

function noFeedEntry(root: ExportRoot): RootChange {
  return { subject: root.subject, state: "no-feed", revision: root.revision, lastChangedAt: null, lastChangedBy: null, action: null };
}

function notRecordedEntry(root: ExportRoot): RootChange {
  return { subject: root.subject, state: "not-recorded", revision: root.revision, lastChangedAt: null, lastChangedBy: null, action: null };
}

/**
 * Classify every export root against the change feed.
 *
 * A root not yet ASKED about (`lookup(subject).asked === false`) gets no
 * entry at all -- not `not-recorded`, which would claim a fact about the feed
 * the browser has not actually asked it for. Once asked, `answer === null`
 * is that root's provider answering no-feed, and EVERY root on that provider
 * is `no-feed`, never `current`: an unanswerable question must not render as
 * a clean bill (the same rule `no-feed`'s own definition states).
 */
export function classifyChanges(roots: readonly ExportRoot[], lookup: RootLookup): ChangeReport {
  if (!roots.length) return EMPTY_REPORT;
  const byRoot = new Map<string, RootChange>();
  let behind = 0;
  let current = 0;
  let notRecorded = 0;
  for (const root of roots) {
    const { asked, answer } = lookup(root.subject);
    if (!asked) continue;
    if (answer === null) {
      byRoot.set(root.subject, noFeedEntry(root));
      continue;
    }
    const row = answer.rows.get(root.subject);
    if (!row) {
      notRecorded++;
      byRoot.set(root.subject, notRecordedEntry(root));
      continue;
    }
    // Strictly greater: a root published in the same instant as the change it
    // already includes is not behind it -- `>=` would mark every export
    // behind its own recorded change.
    let isBehind: boolean;
    try {
      isBehind = compareRevisions(revisionFromInstant(row.lastChangedAt), root.revision) > 0;
    } catch {
      // A malformed instant is the feed's fault, not grounds to alarm the
      // reader over a row they cannot act on -- read as `current` rather
      // than let one bad timestamp throw the whole classification.
      isBehind = false;
    }
    if (isBehind) behind++;
    else current++;
    byRoot.set(root.subject, {
      subject: root.subject,
      state: isBehind ? "behind" : "current",
      revision: root.revision,
      lastChangedAt: row.lastChangedAt,
      lastChangedBy: row.lastChangedBy,
      action: row.action,
    });
  }
  return { byRoot, behind, current, notRecorded };
}

// ---------------------------------------------------------------------------
// per-node evidence: which nodes the sweep actually touched
// ---------------------------------------------------------------------------

/** node ref -> what the sweep recorded for it. Built straight from whatever
 *  rows have been merged into the store so far (`assetBrowserStore.changedRows`)
 *  -- a ref simply absent here was either never asked or asked and found to
 *  carry no `action` (a roll-up ancestor, or a node the feed never mentions),
 *  and both render as nothing: an evidence mark is only ever a positive
 *  claim, never inferred from silence. */
export function evidenceMarks(changedRows: ReadonlyMap<string, SourceNodeRow>): ReadonlyMap<string, ChangeAction> {
  const marks = new Map<string, ChangeAction>();
  for (const [ref, row] of changedRows) if (row.action) marks.set(ref, row.action);
  return marks;
}
