// WHICH PUBLISHED SPINE COVERS A NODE, and whether it is already in.
//
// A subtree `hierarchy.json` is published UNDER A SUBJECT and covers that
// subject's whole subtree. Looking a node's spine up by its own id answers "is a
// spine published AT this node", not "does a published spine CONTAIN this
// node" -- the same thing only when every clickable row is itself a publish
// root. The moment a spine is rooted at a branch whose children arrive from the
// collection index, those children advertise more rows (`leaf: false`), sit
// inside a published file the tab holds the key to, and a direct lookup reads
// them as dead ends. So coverage resolves NEAREST SPINE AT OR ABOVE -- lazily,
// for the rows that ask (see `spineCoverage`).
//
// A subject IS a node id here (core's node-identity contract: single segment,
// stable, unique within the collection), so a spine's `root` and its `subject`
// are the same string; both are kept because they answer different questions --
// "which document" and "which row is its top".

import { HIERARCHY_FILENAME } from "./assetIndex";
import type { Hierarchy } from "./hierarchy";
import type { Resolution } from "./resolve";
import type { AssetNode } from "./types";

export interface SpineSource {
  readonly subject: string;
  /** Part of the identity: the same root at a new revision is a different
   *  document and must be re-read. */
  readonly revision: string;
  readonly root: string;
}

/** The spine published AT a node under the active resolution, if any. The
 *  collection subject is never a node's spine -- its hierarchy is the index. */
export function spineRootedAt(resolution: Resolution, id: string): SpineSource | null {
  if (id === resolution.collection) return null;
  const resolved = resolution.subjects.get(id);
  if (!resolved || !resolved.revision.files.has(HIERARCHY_FILENAME)) return null;
  return { subject: id, revision: resolved.revision.revision, root: id };
}

/** Answers "which spine covers this node" for the rows that ASK.
 *
 * Lazy with a memo, rather than one eager pass over the forest: the questions
 * come from the rows on screen (a few dozen) and the expanded set, never from
 * all 41k rows at once, so an eager map was work nobody read. A walk up stops
 * at the first memoised ancestor and memoises the path it took, so repeated
 * questions under one branch cost one step each. */
export interface SpineLookup {
  get(id: string): SpineSource | undefined;
}

export function spineCoverage(
  h: Hierarchy<AssetNode>,
  rootedAt: (id: string) => SpineSource | null,
): SpineLookup {
  const memo = new Map<string, SpineSource | null>();
  return {
    get(id: string): SpineSource | undefined {
      if (!h.byId.has(id)) return undefined;
      const path: string[] = [];
      let cur: string | null = id;
      let found: SpineSource | null = null;
      const seen = new Set<string>();
      while (cur !== null && !seen.has(cur)) {
        seen.add(cur);
        const hit = memo.get(cur);
        if (hit !== undefined) {
          found = hit;
          break;
        }
        path.push(cur);
        const own = rootedAt(cur);
        if (own) {
          found = own;
          break;
        }
        cur = h.byId.get(cur)?.parent ?? null;
      }
      // Nearest wins: a node below its own spine root inherits that root, and
      // the root itself resolves to its own spine.
      for (const p of path) memo.set(p, found);
      return found ?? undefined;
    },
  };
}

/** Whether the covering spine is merged -- KEYED BY THE SPINE'S ROOT, so the
 *  second branch expanded under one spine does not re-fetch it, and compared by
 *  REVISION, because a resolution change re-points the tree at another document. */
export function spineMerged(source: SpineSource | null, loaded: ReadonlyMap<string, string>): boolean {
  return source !== null && loaded.get(source.root) === source.revision;
}

/** Whether expanding this row would bring something back. `leaf` is load-
 *  bearing both ways: a leaf has nothing beneath by definition, and a
 *  `leaf: false` row is a positive claim that it HAS children. */
export function canFetchSpine(
  node: AssetNode | undefined,
  source: SpineSource | null,
  loaded: ReadonlyMap<string, string>,
): boolean {
  if (!node || node.leaf) return false;
  return source !== null && !spineMerged(source, loaded);
}

export interface RowSpineState {
  readonly loading: boolean;
  readonly error: string | null;
  /** A branch that promises children it can never deliver: `leaf: false`, no
   *  children held, nothing (more) to fetch. Legitimate -- an index can name a
   *  node whose subtree was never published -- and never silent. */
  readonly deadEnd: boolean;
}

/** What one row says about the spine covering it. The mark goes where the wait
 *  is FELT: on a row with nothing under it yet, or on the spine's own root --
 *  not on every already-drawn row a re-read happens to cover. */
export function rowSpineState(input: {
  readonly node: AssetNode | undefined;
  /** The FOREST's answer, not the filtered row's: a branch whose children a
   *  filter removed is filtered, not a dead end. */
  readonly hasChildren: boolean;
  readonly source: SpineSource | null;
  readonly loaded: ReadonlyMap<string, string>;
  readonly loading: ReadonlySet<string>;
  readonly errors: ReadonlyMap<string, string>;
}): RowSpineState {
  const { node, hasChildren, source, loaded, loading, errors } = input;
  const explored = source === null || spineMerged(source, loaded);
  const deadEnd = !!node && !node.leaf && !hasChildren && explored;
  const waiting = source !== null && !!node && !node.leaf && (!hasChildren || source.root === node.id);
  if (source === null || !waiting || spineMerged(source, loaded)) return { loading: false, error: null, deadEnd };
  return { loading: loading.has(source.root), error: errors.get(source.root) ?? null, deadEnd };
}
