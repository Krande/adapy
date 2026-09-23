// THE ONE OBJECT THE ASSETS TAB READS.
//
// Every badge, dimmed row, gap marker, stale mark, drift flag and orphan is
// derived from a single `AssetView`, computed from (forest, index, collection,
// mode). Nothing in the UI resolves a revision for itself.
//
// That is the difference between a tree that is merely populated and a tree
// that is CONSISTENT. If each row resolved its own newest revision, a parent's
// badge could come from one publish, its child's from another, and a Load from
// a third -- and no screenshot of the result would be reproducible. Lifting
// resolution into one context makes "which publish am I looking at" a question
// with one answer, and lets the tab state it.
//
// React-free and side-effect-free: this is where the reasoning is, so it is
// what the tests drive. It knows no provider and no source format -- only core's
// two documents (`asset.json`, summarised by the index route, and
// `hierarchy.json`).
//
// PERFORMANCE SHAPE. The hierarchy depends on the forest only; everything else
// depends on the mode too. So `buildAssetView` accepts a prebuilt hierarchy and
// the tab memoises it on the forest, which keeps a mode switch over a 41k-row
// spine to the O(n) passes below with no re-indexing.

import { HIERARCHY_FILENAME, isComplete } from "./assetIndex";
import {
  classifyChanges,
  evidenceMarks,
  type ChangeAction,
  type ChangeReport,
  type ExportRoot,
  type SourceNodeRow,
  type SourceNodesAnswer,
} from "./changes";
import { projectCoverage, type CoverageResult } from "./coverage";
import { hierarchyDrift, nodeFreshness, staleCount, type HierarchyDrift, type NodeFreshness } from "./freshness";
import { ancestorsOf, buildHierarchyFrom, type Hierarchy } from "./hierarchy";
import type { Forest } from "./merge";
import { lastKnownPath, orphanCause, type OrphanEntry } from "./orphans";
import {
  describeResolution,
  resolveCollection,
  type Resolution,
  type ResolutionSummary,
} from "./resolve";
import { spineCoverage, spineRootedAt, type SpineLookup, type SpineSource } from "./spines";
import type { AssetIndex, AssetNode, AssetRevision, ResolutionMode } from "./types";

/** A subject whose resolved revision carries a delivery claim. Propagates: a
 *  claim at a branch delivers that branch's descendants. */
export const ROLE_CONTENT = "content";
/** A subject whose resolved revision carries its own subtree `hierarchy.json`.
 *  Does NOT propagate: a hierarchy rooted above a row contains that row by
 *  definition, so a ghost for it would sit on every descendant and say nothing. */
export const ROLE_TREE = "tree";
const PROPAGATING: ReadonlySet<string> = Object.freeze(new Set([ROLE_CONTENT]));

/** A revision carries content when its manifest makes a delivery claim.
 *
 * An UNKNOWN manifest (the index could not summarise it) is NOT content: a
 * badge must be a fact, and the revision's error is reported in
 * `manifestErrors` instead, so the row is explained rather than decorated. */
export function carriesContent(rev: AssetRevision): boolean {
  return rev.manifest !== null && rev.manifest.delivery !== "none";
}

export interface AssetViewInput {
  readonly forest: Forest;
  readonly index: AssetIndex;
  readonly collection: string;
  readonly mode: ResolutionMode;
  /** The collection-index revisions actually MERGED into the forest, ascending.
   *  An orphan and a drift are judged against the newest of these -- the tree
   *  on screen -- not against whatever the subject itself resolved to. */
  readonly indexRevisions?: readonly string[];
  /** A hierarchy already built from `forest.nodes`, so a mode switch does not
   *  re-index the forest. MUST be built from the same forest. */
  readonly hierarchy?: Hierarchy<AssetNode>;
  /** Spine root -> revision merged. Decides which branches are UNEXPLORED: a
   *  branch whose published subtree has not been fetched yet cannot be called
   *  "nothing to deliver here" -- that would be a guess about rows nobody has
   *  read. Omitted, every branch counts as explored. */
  readonly spineLoaded?: ReadonlyMap<string, string>;
  /** PROVIDER id -> the change feed's last answer for that provider, or
   *  `null` for that provider's own no-feed. Per provider, not one flat
   *  answer, because a mixed collection can straddle providers with
   *  different feed availability (`./changes`'s `RootLookup`). Omitted, no
   *  root is ever classified -- see `evidenceAsked`. */
  readonly sourceAnswer?: ReadonlyMap<string, SourceNodesAnswer | null>;
  /** Every node ref (a root's own subject id, or any descendant) the browser
   *  has asked the feed about, across every provider. A root not in this set
   *  is not "not-recorded" -- that would claim a fact about the FEED's
   *  coverage the browser has not actually asked it for -- it is simply not
   *  yet classified, the same honesty `unexplored` keeps for spines. */
  readonly evidenceAsked?: ReadonlySet<string>;
  /** Every row the feed has told us carries a non-null `action`, flattened
   *  across providers -- the per-node evidence marks. Kept as its OWN input,
   *  not derived from `sourceAnswer` here, because the store already
   *  maintains this flattening incrementally (`assetBrowserStore.changedRows`)
   *  as evidence trickles in per spine, and re-flattening on every view build
   *  would repeat that work for nothing a view needs to decide itself. */
  readonly changedRows?: ReadonlyMap<string, SourceNodeRow>;
}

export interface AssetView {
  readonly collection: string;
  readonly hierarchy: Hierarchy<AssetNode>;
  readonly resolution: Resolution;
  readonly summary: ResolutionSummary;
  readonly coverage: CoverageResult;
  /** node id -> the nearest published spine at or above it (lazy, memoised). */
  readonly spines: SpineLookup;
  /** Published subjects no loaded spine contains, with the cause and last
   *  known place. Listed under the collection root, never dropped. */
  readonly orphans: readonly OrphanEntry[];
  /** The newest merged collection index, or null when none is merged. */
  readonly spineRevision: string | null;
  readonly freshness: ReadonlyMap<string, NodeFreshness>;
  readonly staleCount: number;
  /** Rows with an unfetched published subtree at or below them. Never
   *  `dimmed`: absence of payload there is not yet known. */
  readonly unexplored: ReadonlySet<string>;
  /** Published spines on screen that are not merged at the revision the
   *  resolution names -- what "place everything" would fetch. */
  readonly unmergedSpines: readonly SpineSource[];
  /** Published subjects with no row YET, while spines that might contain them
   *  are still unfetched. Not orphans: calling a subject "removed" or "ahead"
   *  because its branch was never opened would be a guess stated as a fact. */
  readonly pending: readonly string[];
  /** subject -> "published against an older tree". Only subjects that drift. */
  readonly drift: ReadonlyMap<string, HierarchyDrift>;
  /** Distinct producing providers among the loaded rows. More than one is a
   *  MIXED collection -- first class, and worth a legend. */
  readonly providers: readonly string[];
  /** subject -> why its resolved manifest could not be read. */
  readonly manifestErrors: ReadonlyMap<string, string>;
  readonly malformedKeys: readonly string[];
  /** Behind-upstream, per export root -- the change feed's answer, a
   *  DIFFERENT fact from `freshness` above and never folded into it (see the
   *  module comment in `./changes`). */
  readonly changes: ChangeReport;
  /** node id -> what the sweep found there (`added`/`modified`/`deleted`).
   *  Per-node evidence, kept apart from `changes.byRoot`'s per-root states --
   *  §Decision 7's "adopted as per-node evidence vocabulary only". */
  readonly evidenceMarks: ReadonlyMap<string, ChangeAction>;
  /** True once at least one resolved manifest in this view carries a
   *  `change` with an actor (`publishedBy` or `sourceActor`). The "changed
   *  by" filter is offered only then (§Decision 6: absent is normal, and a
   *  filter over nothing is worse than no filter). */
  readonly hasChangeOwners: boolean;
}

export function buildAssetHierarchy(forest: Forest): Hierarchy<AssetNode> {
  // The forest is already de-duplicated by its merge, so index it directly.
  return buildHierarchyFrom(forest.nodes, (n) => n.parent, (n) => n);
}

export function buildAssetView(input: AssetViewInput): AssetView {
  const { forest, index, collection, mode } = input;
  const hierarchy = input.hierarchy ?? buildAssetHierarchy(forest);
  const isCollectionSubject = (s: string) => s === collection;

  const resolution = resolveCollection(index, collection, mode, { carriesContent });

  const rootedRoles = new Map<string, Set<string>>();
  const publishedSubjects: string[] = [];
  const manifestErrors = new Map<string, string>();
  for (const [subject, resolved] of resolution.subjects) {
    if (resolved.revision.manifestError) manifestErrors.set(subject, resolved.revision.manifestError);
    if (isCollectionSubject(subject)) continue;
    publishedSubjects.push(subject);
    const roles = new Set<string>();
    if (resolved.content) roles.add(ROLE_CONTENT);
    if (resolved.revision.files.has(HIERARCHY_FILENAME)) roles.add(ROLE_TREE);
    if (roles.size) rootedRoles.set(subject, roles);
  }

  const coverage = projectCoverage(hierarchy, {
    rootedRoles,
    publishedSubjects,
    propagating: PROPAGATING,
    // Payload = a leaf: the thing a publish would end up delivering. Only
    // zero-vs-nonzero decides `dimmed`; the count feeds collapsed-branch labels.
    payloadOf: (node) => (node.data.leaf ? 1 : 0),
  });

  const spines = spineCoverage(hierarchy, (id) => spineRootedAt(resolution, id));
  // Walked from the UNMERGED spine roots rather than folded over every row: on
  // a settled tree there are none, and the cost of this pass should scale with
  // what is still to fetch, not with the 41k rows already in.
  const unexplored = new Set<string>();
  const unmergedSpines: SpineSource[] = [];
  if (input.spineLoaded) {
    const loaded = input.spineLoaded;
    for (const [subject, resolved] of resolution.subjects) {
      if (!hierarchy.byId.has(subject)) continue;
      const source = spineRootedAt(resolution, subject);
      if (!source || loaded.get(source.root) === resolved.revision.revision) continue;
      unmergedSpines.push(source);
      // The root, every non-leaf row the forest already holds under it, and
      // every ancestor above it: none of them can yet say "nothing below".
      const stack = [subject];
      while (stack.length) {
        const id = stack.pop()!;
        if (hierarchy.byId.get(id)?.data.leaf || unexplored.has(id)) continue;
        unexplored.add(id);
        for (const c of hierarchy.childrenOf(id)) stack.push(c);
      }
      for (const a of ancestorsOf(hierarchy, subject)) unexplored.add(a);
    }
  }

  const merged = input.indexRevisions ?? [];
  const spineRevision = merged.length ? merged[merged.length - 1] : null;

  const labelOf = (n: AssetNode) => n.label || n.id;
  const orphans: OrphanEntry[] = [];
  // An absent subject is an orphan only once nothing still unfetched could hold
  // it. Until then it is PENDING: the honest statement is "not placed yet".
  const pending: string[] = unmergedSpines.length ? [...coverage.orphanSubjects] : [];
  if (spineRevision && !unmergedSpines.length) {
    for (const id of coverage.orphanSubjects) {
      const revision = resolution.subjects.get(id)?.revision.revision;
      if (!revision) continue;
      orphans.push({
        id,
        revision,
        spineRevision,
        cause: orphanCause(revision, spineRevision),
        path: lastKnownPath(hierarchy, id, (x) => forest.retired.get(x) ?? null, labelOf),
      });
    }
  }

  // Rows from the collection index are current when their index revision is one
  // the mode still merges (the index is a union); every other row is current
  // when its spine's subject still resolves to the revision it was drawn at.
  const mergedSet = new Set(merged);
  const freshness = nodeFreshness(forest.origins, (origin) => {
    if (isCollectionSubject(origin.subject)) {
      return mergedSet.has(origin.revision) ? origin.revision : spineRevision;
    }
    return resolution.subjects.get(origin.subject)?.revision.revision ?? null;
  });

  const drift = new Map<string, HierarchyDrift>();
  for (const [subject, resolved] of resolution.subjects) {
    if (isCollectionSubject(subject) || !isComplete(resolved.revision)) continue;
    const d = hierarchyDrift(resolved.revision.manifest?.hierarchyRevision ?? null, spineRevision);
    if (d) drift.set(subject, d);
  }

  const providers = new Set<string>();
  for (const n of forest.nodes.values()) providers.add(n.provider);

  // Export roots: every published subject this resolution names, other than
  // the collection index itself (which is not a publish anyone re-exports).
  // A tree-only publish is still a legal root (§Decision 3) -- carrying
  // `content` is not the bar, being a resolved SUBJECT is.
  const roots: ExportRoot[] = publishedSubjects.map((subject) => ({
    subject,
    revision: resolution.subjects.get(subject)!.revision.revision,
  }));
  const evidenceAsked = input.evidenceAsked;
  const sourceAnswer = input.sourceAnswer;
  const changes = classifyChanges(roots, (subject) => {
    if (!evidenceAsked?.has(subject)) return { asked: false, answer: null };
    const provider = hierarchy.byId.get(subject)?.data.provider;
    // Defensive fallback, not an expected path: `evidenceAsked` and
    // `sourceAnswer` are updated together by the same store action per
    // provider group, so a subject marked asked always has its provider's
    // entry too. Reading a mismatch as no-feed -- rather than `current` --
    // is the same "an unanswerable question must not look clean" rule the
    // real no-feed case follows.
    const answer = provider !== undefined ? (sourceAnswer?.get(provider) ?? null) : null;
    return { asked: true, answer };
  });
  const evidence = evidenceMarks(input.changedRows ?? new Map());

  let hasChangeOwners = false;
  for (const resolved of resolution.subjects.values()) {
    const c = resolved.revision.manifest?.change;
    if (c && (c.publishedBy || c.sourceActor)) {
      hasChangeOwners = true;
      break;
    }
  }

  return {
    collection,
    hierarchy,
    resolution,
    // The collection subject is re-stamped by every publish; counting it would
    // make every leaf-only publish read as `mixed` against the index.
    summary: describeResolution(resolution, isCollectionSubject),
    coverage,
    spines,
    unexplored,
    unmergedSpines,
    pending,
    orphans,
    spineRevision,
    freshness,
    staleCount: staleCount(freshness),
    drift,
    providers: [...providers].sort(),
    manifestErrors,
    malformedKeys: index.malformed,
    changes,
    evidenceMarks: evidence,
    hasChangeOwners,
  };
}
