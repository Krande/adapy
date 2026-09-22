// Project a set of published subjects onto a hierarchy and derive, per node,
// what the row has to show.
//
// Generic. Roles are opaque strings here — this module does not know what any
// provider publishes, only which roles it was told propagate.
//
// FOUR OUTPUTS, and two of them are the reason this module exists.
//
//   rooted   a role published AT this node. Solid badge.
//   covered  a role published at an ANCESTOR, which therefore includes this
//            node. Ghost badge. Only roles declared `propagating` do this: an
//            export rooted above me contains my geometry, but a HIERARCHY
//            export rooted above me contains my row too — so a ghost for it
//            would sit on literally every descendant row and carry no
//            information. Non-propagating roles are shown only where rooted.
//
//   dimmed   no payload at or below this node. "There is nothing here to
//            export." On one real collection measured, three of five branches are
//            exactly this, so it is the common case, not an edge case.
//            A dimmed row must still be RENDERED — hiding it is what makes a
//            user hunt for an export that was never possible.
//   gap      payload at or below this node that NO publish covers -- neither one
//            at or above this row, nor one on the payload's own line below it.
//            "There is something here and nobody has exported it." This is the
//            actionable one. Counted per payload, not decided per row: a branch
//            whose every leaf is published leaf-by-leaf has no gap, although
//            nothing is published at the branch itself.
//
// `dimmed` and `gap` are complementary halves of the same question and both are
// derived, never authored. There is deliberately NO is-asset-root marker
// anywhere in this design: nobody in the pipeline has the authority to write
// one, so a stored flag would be somebody's guess dressed as a fact.

import type { Hierarchy, HierarchyNode } from "./hierarchy";

export interface CoverageInput<T> {
  /** node id -> the roles published AT that node under the active resolution.
   *  Subjects that name a node the hierarchy does not contain are ignored here
   *  and reported by `orphanSubjects` instead. */
  readonly rootedRoles: ReadonlyMap<string, ReadonlySet<string>>;
  /** Every subject that resolved to a published entry, whether or not it earns
   *  a badge. Orphan-hood means "a published entry whose ref matches no row" --
   *  which is independent of which badges the entry happens to earn. Tying the
   *  two together made an entry with no badge vanish from the report entirely,
   *  and an entry nothing can see is the failure this whole panel exists to
   *  remove. Defaults to the keys of `rootedRoles`. */
  readonly publishedSubjects?: Iterable<string>;
  /** Roles whose coverage extends to descendants. A role NOT listed here is
   *  shown only where it is rooted. */
  readonly propagating: ReadonlySet<string>;
  /** How much exportable payload sits at this node itself. Zero for a
   *  container. The units are the caller's; only zero-vs-nonzero is used for
   *  `dimmed`, while the count is passed through for the row's own wording
   *  ("14 members here, no export covers them"). */
  readonly payloadOf: (node: HierarchyNode<T>) => number;
}

export interface NodeCoverage {
  /** Roles published at this exact node. */
  readonly rooted: ReadonlySet<string>;
  /** Propagating roles inherited from the nearest ancestors that publish them,
   *  minus anything already rooted here (a role is never both weights). */
  readonly covered: ReadonlySet<string>;
  /** The nearest ancestor that publishes each covered role — what the row's
   *  "Covered by the export rooted at ..." sentence names. */
  readonly coveredBy: ReadonlyMap<string, string>;
  /** Propagating roles rooted STRICTLY BELOW this node, minus anything already
   *  rooted or covered here (a role is never two weights at once).
   *
   * The third direction, and the one a collapsed tree needs most. `rooted` and
   * `covered` both answer from a node's own line -- what is here, what reaches
   * me from above -- so neither can be seen without expanding to the row that
   * has it. A root with an export three levels beneath it therefore looked
   * exactly like a root with nothing in it, and the only way to tell them apart
   * was to open every branch: the question the browser exists to answer without
   * doing that.
   *
   * Restricted to `propagating` for the reason coverage is: a role describing
   * CONTENT is worth announcing upward, while `tree` is rooted at every root of
   * a sweep and would mark every ancestor of everything. */
  readonly below: ReadonlySet<string>;
  /** The shallowest descendant rooting each role in `below`, so a tooltip can
   *  name where to look rather than only that there is somewhere. */
  readonly belowBy: ReadonlyMap<string, string>;
  readonly payloadSelf: number;
  readonly payloadSubtree: number;
  /** Payload at or below this node that no propagating role covers. */
  readonly uncoveredSubtree: number;
  /** payloadSubtree === 0. */
  readonly dimmed: boolean;
  /** uncoveredSubtree > 0. */
  readonly gap: boolean;
}

export interface CoverageResult {
  readonly byId: ReadonlyMap<string, NodeCoverage>;
  /** Subjects that resolved to something but name a node no loaded spine
   *  contains. These are NOT dropped: an export published after the last
   *  hierarchy sweep is a real, common state, and dropping it means the user
   *  sees no trace of an asset that exists. The tab lists them under the
   *  collection root with its cause (./orphans). */
  readonly orphanSubjects: readonly string[];
}

const EMPTY_SET: ReadonlySet<string> = Object.freeze(new Set<string>());
const EMPTY_MAP: ReadonlyMap<string, string> = Object.freeze(new Map<string, string>());

/** The per-node record while it is being built. Exposed as `NodeCoverage`
 *  (readonly) once both passes are done. `anyCoverage` is internal. */
interface Draft {
  rooted: ReadonlySet<string>;
  covered: ReadonlySet<string>;
  coveredBy: ReadonlyMap<string, string>;
  below: ReadonlySet<string>;
  belowBy: ReadonlyMap<string, string>;
  payloadSelf: number;
  payloadSubtree: number;
  uncoveredSubtree: number;
  dimmed: boolean;
  gap: boolean;
  /** What this node hands its children: inherited, overridden by its own roots. */
  downward: ReadonlyMap<string, string>;
  anyCoverage: boolean;
}

export function projectCoverage<T>(
  h: Hierarchy<T>,
  input: CoverageInput<T>,
): CoverageResult {
  // TWO PASSES, O(n) each, and the shape of both is what makes a 41k-row spine
  // re-derive on a mode switch without a visible pause:
  //
  //   down  (in `order`, parent before child) -- what a node inherits and shows
  //         as ghosts. Each node reads its parent's finished record rather than
  //         walking its own ancestor chain.
  //   up    (reverse `order`, children before parent) -- payload totals, the
  //         `below` announcements, and the two flags that depend on the total
  //         (`dimmed`, `gap`). One pass for all three, where a separate
  //         subtree fold used to be a third walk and a 41k-entry map.
  const byId = new Map<string, Draft>();
  const ghostsOf = new Map<
    ReadonlyMap<string, string>,
    { covered: ReadonlySet<string>; coveredBy: ReadonlyMap<string, string> }
  >();

  for (const id of h.order) {
    const node = h.byId.get(id) as HierarchyNode<T>;
    const rooted = input.rootedRoles.get(id) ?? EMPTY_SET;
    const fromParent = (node.parent !== null ? byId.get(node.parent)?.downward : undefined) ?? EMPTY_MAP;

    // What THIS node passes down: whatever it inherited, overridden by any
    // propagating role it roots itself — nearest ancestor wins, which is what
    // makes `coveredBy` name the closest publish rather than the outermost one.
    let downward = fromParent;
    let rootedPropagating = false;
    for (const role of rooted) {
      if (!input.propagating.has(role)) continue;
      if (!rootedPropagating) {
        downward = new Map(fromParent);
        rootedPropagating = true;
      }
      (downward as Map<string, string>).set(role, id);
    }

    // What this node SHOWS as ghosts: inherited roles it does not root itself.
    //
    // SHARED, NOT PER-ROW, in the common case. A row that roots none of the
    // roles it inherits shows exactly its parent's `downward` map -- and every
    // sibling under that parent shows the same one. Allocating a Set and a Map
    // per row made this pass dominate a 41k-row re-derive; one pair per
    // distinct `fromParent` makes it an allocation per PUBLISH ROOT instead.
    let covered: ReadonlySet<string> = EMPTY_SET;
    let coveredBy: ReadonlyMap<string, string> = EMPTY_MAP;
    if (fromParent.size) {
      let overlaps = false;
      for (const role of rooted) if (fromParent.has(role)) overlaps = true;
      if (!overlaps) {
        let shared = ghostsOf.get(fromParent);
        if (!shared) {
          shared = { covered: new Set(fromParent.keys()), coveredBy: fromParent };
          ghostsOf.set(fromParent, shared);
        }
        covered = shared.covered;
        coveredBy = shared.coveredBy;
      } else {
        const set = new Set<string>();
        const by = new Map<string, string>();
        for (const [role, owner] of fromParent) {
          if (rooted.has(role)) continue;
          set.add(role);
          by.set(role, owner);
        }
        if (set.size) {
          covered = set;
          coveredBy = by;
        }
      }
    }

    const payloadSelf = input.payloadOf(node);
    // "Covered at or above" is exactly "the parent handed something down, or I
    // root something propagating myself".
    const anyCoverage = fromParent.size > 0 || rootedPropagating;
    byId.set(id, {
      rooted,
      covered,
      coveredBy,
      below: EMPTY_SET,
      belowBy: EMPTY_MAP,
      payloadSelf,
      // Final for a childless node; the upward pass adds the children's.
      payloadSubtree: payloadSelf,
      uncoveredSubtree: anyCoverage ? 0 : payloadSelf,
      dimmed: payloadSelf === 0,
      gap: payloadSelf > 0 && !anyCoverage,
      downward,
      anyCoverage,
    });
  }

  // SHALLOWEST WINS for `below`, so a child's own roots are considered before
  // the deeper things that child merely reports: `belowBy` names the first
  // publish you would meet on the way down, which is the one worth opening to.
  for (let i = h.order.length - 1; i >= 0; i--) {
    const id = h.order[i];
    const kids = h.childrenOf(id);
    if (!kids.length) continue; // a leaf's record is already final
    const self = byId.get(id) as Draft;

    let total = self.payloadSelf;
    let uncovered = self.anyCoverage ? 0 : self.payloadSelf;
    // Allocated only when a child actually reports something: most rows of a
    // large spine have nothing below, and a Map per row was this pass's cost.
    let by: Map<string, string> | null = null;
    for (const child of kids) {
      const cov = byId.get(child);
      if (!cov) continue;
      total += cov.payloadSubtree;
      uncovered += cov.uncoveredSubtree;
      for (const role of cov.rooted) {
        if (!input.propagating.has(role)) continue;
        by ??= new Map<string, string>();
        if (!by.has(role)) by.set(role, child);
      }
    }
    for (const child of kids) {
      const cov = byId.get(child);
      if (!cov || !cov.belowBy.size) continue;
      by ??= new Map<string, string>();
      for (const [role, owner] of cov.belowBy) if (!by.has(role)) by.set(role, owner);
    }
    self.payloadSubtree = total;
    self.uncoveredSubtree = uncovered;
    self.dimmed = total === 0;
    self.gap = uncovered > 0;
    if (by === null) continue;
    // A role already answered on this row is not also announced from below: the
    // weights are exclusive, and "here" is always the more useful statement.
    for (const role of self.rooted) by.delete(role);
    for (const role of self.covered) by.delete(role);
    if (by.size) {
      self.below = new Set(by.keys());
      self.belowBy = by;
    }
  }

  const orphanSubjects: string[] = [];
  for (const subject of input.publishedSubjects ?? input.rootedRoles.keys()) {
    if (!h.byId.has(subject)) orphanSubjects.push(subject);
  }
  orphanSubjects.sort();

  // The drafts carry two internal fields (`downward`, `anyCoverage`) beyond
  // `NodeCoverage`; handing them out as the readonly type keeps them internal
  // without a 41k-object copy.
  return { byId: byId as ReadonlyMap<string, NodeCoverage>, orphanSubjects };
}

/** The nearest ancestor (or `id` itself) satisfying `pred`, or null.
 *
 * The primitive behind "resolve this node to the root that contains it" and
 * "which export would a Load here actually use". Kept here rather than in a
 * caller so both questions are answered by the same walk. */
export function nearestSelfOrAncestor<T>(
  h: Hierarchy<T>,
  id: string,
  pred: (node: HierarchyNode<T>) => boolean,
): string | null {
  const seen = new Set<string>();
  let cur: string | null = id;
  while (cur !== null && !seen.has(cur)) {
    seen.add(cur);
    const node: HierarchyNode<T> | undefined = h.byId.get(cur);
    if (!node) return null;
    if (pred(node)) return cur;
    cur = node.parent;
  }
  return null;
}
