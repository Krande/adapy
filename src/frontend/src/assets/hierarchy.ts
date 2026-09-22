// A parent-pointer forest, indexed for the three questions the tree asks:
// "who are my children", "who are my ancestors", and "what is the flat row list
// for the current expansion".
//
// Generic: a node here has an id, a parent and an
// opaque `data` payload — this module never looks inside `data`.
//
// TWO PROPERTIES THAT ARE NOT DEFENSIVE PROGRAMMING, they are the normal case:
//
//   * The forest is ASSEMBLED INCREMENTALLY. The tab opens on a collection index
//     (depth 1: the declared roots) and fetches each root's full spine only when
//     it is expanded, because one spine can reach ~41k nodes and a collection
//     has many. So at any moment most roots are leaves-of-the-known-world, and
//     a node whose declared parent is not present yet is normal, not corrupt.
//   * A node whose parent is ABSENT is promoted to a root rather than dropped.
//     Dropping it is how a published subject at a node no spine contains becomes
//     invisible, which is precisely the "stale spine" state the tab is
//     required to show.
//
// Recursion is avoided throughout. A measured spine is 5 deep, but the module
// makes no assumption about that and a 41k-node chain must not overflow.

export interface HierarchyInput<T> {
  readonly id: string;
  readonly parent: string | null;
  readonly data: T;
}

export interface HierarchyNode<T> {
  readonly id: string;
  /** The EFFECTIVE parent: null when the declared parent is absent from the
   *  forest, so a subtree with an unfetched parent stands on its own. */
  readonly parent: string | null;
  /** What the source said, kept so "this node's parent is in no spine we hold"
   *  stays distinguishable from "this node is genuinely a root". */
  readonly declaredParent: string | null;
  readonly depth: number;
  readonly data: T;
}

export interface Hierarchy<T> {
  readonly byId: ReadonlyMap<string, HierarchyNode<T>>;
  readonly childrenOf: (id: string) => readonly string[];
  readonly roots: readonly string[];
  /** Every id in an order where a parent always precedes its children.
   *  `foldSubtrees` walks it backwards to aggregate bottom-up in one pass. */
  readonly order: readonly string[];
}

const NO_CHILDREN: readonly string[] = Object.freeze([]);

export function buildHierarchy<T>(input: readonly HierarchyInput<T>[]): Hierarchy<T> {
  const declared = new Map<string, HierarchyInput<T>>();
  // Last writer wins on a duplicate id. Two spines can legitimately carry the
  // same node (a collection index lists the roots, and so does each root's own
  // spine); taking the later one means the richer per-root row replaces the
  // stub rather than the other way round.
  //
  // EXCEPT for a parent that a later row does not know. A subtree document is
  // self-contained, so it names its own top with NO parent -- correct for that
  // document, and a lie about the forest. Letting it win re-parents the node to
  // null, promotes it to a root, and drops it to the bottom of the tree the
  // moment it is expanded. "Richer wins" is the intent; a null parent is not
  // richer, so it does not overwrite a known one.
  for (const n of input) {
    const prev = declared.get(n.id);
    declared.set(
      n.id,
      prev && n.parent === null && prev.parent !== null ? { ...n, parent: prev.parent } : n,
    );
  }
  return buildHierarchyFrom(declared, (n) => n.parent, (n) => n.data);
}

/** The same forest, built straight from a map that is ALREADY de-duplicated by
 *  id (the asset forest is: its merge applies the rules above). Skips the copy
 *  into a second 41k-entry map, which was a third of the build. */
export function buildHierarchyFrom<S, T>(
  declared: ReadonlyMap<string, S>,
  parentOf: (s: S) => string | null,
  dataOf: (s: S) => T,
): Hierarchy<T> {
  const children = new Map<string, string[]>();
  const roots: string[] = [];
  for (const [id, n] of declared) {
    const p = parentOf(n);
    const parent = p !== null && declared.has(p) ? p : null;
    if (parent === null) {
      roots.push(id);
    } else {
      const bucket = children.get(parent);
      if (bucket) bucket.push(id);
      else children.set(parent, [id]);
    }
  }

  // Breadth-first from the roots gives a parent-before-child order and assigns
  // depth in the same pass. No visited-set is needed: each id sits in exactly
  // one parent's bucket, so a walk from the roots meets it at most once, and a
  // cyclic parent chain is simply unreachable from any root -- its members never
  // enter `order` and are appended below as their own roots. The queue is two
  // parallel arrays rather than an object per entry; at 41k rows both the
  // objects and the set were measurable.
  const byId = new Map<string, HierarchyNode<T>>();
  const order: string[] = [];
  const qId: string[] = roots.slice();
  const qDepth: number[] = new Array(roots.length).fill(0);
  for (let head = 0; head < qId.length; head++) {
    const id = qId[head];
    const depth = qDepth[head];
    const n = declared.get(id) as S;
    const p = parentOf(n);
    byId.set(id, {
      id,
      parent: p !== null && declared.has(p) ? p : null,
      declaredParent: p,
      depth,
      data: dataOf(n),
    });
    order.push(id);
    const kids = children.get(id);
    if (!kids) continue;
    for (const child of kids) {
      qId.push(child);
      qDepth.push(depth + 1);
    }
  }
  // Anything a cycle kept out of the walk. Recorded rather than lost, at depth
  // 0, so corrupt data shows up as a stray root instead of a missing row.
  const extraRoots: string[] = [];
  if (byId.size < declared.size) {
    for (const [id, n] of declared) {
      if (byId.has(id)) continue;
      byId.set(id, { id, parent: null, declaredParent: parentOf(n), depth: 0, data: dataOf(n) });
      order.push(id);
      extraRoots.push(id);
    }
  }

  const allRoots = extraRoots.length ? [...roots, ...extraRoots] : roots;
  return {
    byId,
    childrenOf: (id) => children.get(id) ?? NO_CHILDREN,
    roots: allRoots,
    order,
  };
}

/** Ancestors of `id`, NEAREST FIRST, excluding `id` itself.
 *
 * Nearest-first is what every caller wants: "the closest root above me", "the
 * closest published export above me". Bounded by the forest size so a cycle
 * that survived `buildHierarchy` still cannot hang the UI. */
export function ancestorsOf<T>(h: Hierarchy<T>, id: string): string[] {
  const out: string[] = [];
  const seen = new Set<string>([id]);
  let cur = h.byId.get(id)?.parent ?? null;
  while (cur !== null && !seen.has(cur)) {
    out.push(cur);
    seen.add(cur);
    cur = h.byId.get(cur)?.parent ?? null;
  }
  return out;
}

/** Aggregate a per-node value bottom-up: total(n) = self(n) + sum of children.
 *
 * One reverse pass over `order`, so O(n) with no recursion. This is how "does
 * anything under here have geometry" is answered for 41k nodes on every change
 * of the resolution context. */
export function foldSubtrees<T>(
  h: Hierarchy<T>,
  self: (node: HierarchyNode<T>) => number,
): Map<string, number> {
  const totals = new Map<string, number>();
  for (let i = h.order.length - 1; i >= 0; i--) {
    const id = h.order[i];
    const node = h.byId.get(id) as HierarchyNode<T>;
    let total = self(node);
    for (const child of h.childrenOf(id)) total += totals.get(child) ?? 0;
    totals.set(id, total);
  }
  return totals;
}

export interface FlatRow {
  readonly id: string;
  readonly depth: number;
  readonly hasChildren: boolean;
  readonly expanded: boolean;
}

/** The visible rows, in display order, for the current expansion set.
 *
 * This is the input to the virtualiser: the tab slices a window out of it and
 * renders only that. Iterative with an explicit stack — a 41k-node spine fully
 * expanded is a real thing a user can do.
 *
 * A node whose children have not been FETCHED yet is indistinguishable here
 * from a node with no children; this module only knows what it was given. The
 * caller supplies `expandable` when it can tell the difference (a root row
 * whose spine has not been pulled).
 *
 * `include` restricts the output to a subset — the tab's "assets only"
 * filter. IT MUST BE ANCESTOR-CLOSED: `depth` is the node's depth in the WHOLE
 * forest, not in the filtered one, so a kept node whose parent was dropped
 * would render indented under a row that is not there. Building the set with
 * `foldSubtrees` gives that closure by construction, which is why there is no
 * closure step here — a filter that needs one has computed the wrong set. */
export function flattenVisible<T>(
  h: Hierarchy<T>,
  expanded: ReadonlySet<string>,
  opts?: {
    readonly expandable?: (id: string) => boolean;
    readonly include?: ReadonlySet<string>;
  },
): FlatRow[] {
  const out: FlatRow[] = [];
  const expandable = opts?.expandable;
  const include = opts?.include;
  const visible = (id: string) => include === undefined || include.has(id);
  // Reversed so the stack pops in source order.
  const stack: string[] = [...h.roots].filter(visible).reverse();
  while (stack.length) {
    const id = stack.pop() as string;
    const node = h.byId.get(id);
    if (!node) continue;
    // Filtered before `hasChildren` is decided, so a row whose every child was
    // filtered out loses its twisty instead of offering an expansion that
    // reveals nothing.
    const kids = include === undefined ? h.childrenOf(id) : h.childrenOf(id).filter(visible);
    const hasChildren = kids.length > 0 || (expandable ? expandable(id) : false);
    const isExpanded = expanded.has(id);
    out.push({ id, depth: node.depth, hasChildren, expanded: isExpanded && hasChildren });
    if (isExpanded && kids.length) {
      for (let i = kids.length - 1; i >= 0; i--) stack.push(kids[i]);
    }
  }
  return out;
}
