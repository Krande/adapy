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
import type { NodeOrigin } from "./freshness";
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

// --- one LEVEL at a time ------------------------------------------------------
//
// A spine can be a whole site -- hundreds of thousands of rows, tens of MB -- so
// it is never fetched whole to open one row. Expanding a row fetches ONE LEVEL:
// that row's direct children, out of the spine that holds them (the tree
// route's `parent=`), each carrying its own child count so the next level
// knows whether it can open. So what is loaded is tracked per (subject,
// revision, node), not per spine.

/** One level to fetch: the direct children of `node`, from `subject`'s spine at
 *  `revision`. `node === subject` is the spine's first level. */
export interface LevelRequest {
  readonly subject: string;
  readonly revision: string;
  readonly node: string;
}

/** The store's key for a level. The revision is part of it: the same node in a
 *  new revision of its spine is a different question. */
export function levelKey(req: LevelRequest): string {
  return `${req.subject}\u0000${req.revision}\u0000${req.node}`;
}

/** The first level of the spine published AT `id`, if there is one. */
export function firstLevel(resolution: Resolution, id: string): LevelRequest | null {
  const own = spineRootedAt(resolution, id);
  return own ? { subject: id, revision: own.revision, node: id } : null;
}

/** WHICH SPINE holds a row's children, and at which revision.
 *
 * A row that is itself a published subject with a spine owns its children: its
 * first level comes from its own spine, at the revision the resolution names.
 * Any other row's children sit in the spine the row itself came from (its
 * forest ORIGIN), at the revision it was drawn from -- not the subject's
 * current one: a row drawn from an older revision opens from that revision
 * (and says `stale`), so a level never lands in a tree it does not belong to.
 * Null when neither applies (a row no spine contributed). */
export function levelOwner(
  resolution: Resolution,
  origins: ReadonlyMap<string, NodeOrigin>,
  id: string,
): LevelRequest | null {
  const own = firstLevel(resolution, id);
  if (own) return own;
  const origin = origins.get(id);
  if (!origin || origin.subject === id) return null;
  return { subject: origin.subject, revision: origin.revision, node: id };
}

/** Whether this row's level below still has to be FETCHED -- and so whether it
 *  can expand with nothing under it yet. `leaf` is load-bearing both ways: a
 *  leaf has nothing beneath by definition, and a `leaf: false` row is a
 *  positive claim that it HAS children.
 *
 * A subject's own first level is wanted until it is loaded at the resolved
 * revision -- even with rows already under it (from an index, or an older
 * revision): its spine is the authority on them. Any other row wants its level
 * when the spine that drew it counted children (`children > 0`) and none are
 * held yet. A row from a WHOLE spine carries no count and wants nothing: a
 * whole spine held its children already, so an empty one is a real dead end. */
export function levelWanted(
  node: AssetNode | undefined,
  req: LevelRequest | null,
  loaded: ReadonlySet<string>,
  hasChildren: boolean,
): boolean {
  if (!node || node.leaf || !req) return false;
  if (loaded.has(levelKey(req))) return false;
  if (req.node === req.subject) return true;
  return (node.children ?? 0) > 0 && !hasChildren;
}

export interface RowLevelState {
  readonly loading: boolean;
  readonly error: string | null;
  /** A branch that promises children it can never deliver: `leaf: false`, no
   *  children held, nothing (more) to fetch. Legitimate -- an index can name a
   *  node whose subtree was never published -- and never silent. */
  readonly deadEnd: boolean;
}

/** What one row says about the level below it. The wait is shown on the row
 *  whose level is being fetched, and only while it is. */
export function rowLevelState(input: {
  readonly node: AssetNode | undefined;
  /** The FOREST's answer, not the filtered row's: a branch whose children a
   *  filter removed is filtered, not a dead end. */
  readonly hasChildren: boolean;
  readonly req: LevelRequest | null;
  readonly loaded: ReadonlySet<string>;
  readonly loading: ReadonlySet<string>;
  readonly errors: ReadonlyMap<string, string>;
}): RowLevelState {
  const { node, hasChildren, req, loaded, loading, errors } = input;
  const wanted = levelWanted(node, req, loaded, hasChildren);
  const deadEnd = !!node && !node.leaf && !hasChildren && !wanted;
  if (!wanted || !req) return { loading: false, error: null, deadEnd };
  const key = levelKey(req);
  return { loading: loading.has(key), error: errors.get(key) ?? null, deadEnd };
}
