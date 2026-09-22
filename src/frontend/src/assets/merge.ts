// Merge an arriving hierarchy slice into the forest the tab holds.
//
// Pure, so the one rule with data-loss potential is testable without a store.
//
// A merge is mostly a UNION -- later wins, so a richer per-root row replaces a
// collection index's stub. But a union alone can never drop a row, and a node
// removed at the source would then outlive every publish that mentioned it.
// Deleting on absence is sound only inside a document that CLAIMS completeness,
// and exactly three conditions establish one:
//
//   root !== null       a subtree spine carries its root's whole subtree. A
//                       collection index is depth-bounded and claims nothing,
//                       so it never prunes.
//   same subject        a row stamped with this subject was put there by an
//                       older fetch of THIS spine. Rows another subject
//                       contributed (a nested spine, the index stub) are that
//                       subject's to retire.
//   different revision  a re-merge of the revision already held says nothing
//                       new; treating it as authority to delete would turn a
//                       redundant fetch into data loss.

import type { NodeOrigin } from "./freshness";
import type { AssetNode } from "./types";

export interface SpineMerge {
  readonly subject: string;
  readonly revision: string;
  /** The slice's declared root, or null for a collection index. */
  readonly root: string | null;
}

export interface Forest {
  readonly nodes: ReadonlyMap<string, AssetNode>;
  readonly origins: ReadonlyMap<string, NodeOrigin>;
  /** id -> the parent a PRUNED row last had. What lets an orphan with cause
   *  `removed` say where it used to sit, after the row itself has gone. */
  readonly retired: ReadonlyMap<string, string | null>;
}

export const EMPTY_FOREST: Forest = Object.freeze({
  nodes: new Map<string, AssetNode>(),
  origins: new Map<string, NodeOrigin>(),
  retired: new Map<string, string | null>(),
});

/** The merged forest, or the SAME object when the slice changes nothing -- so a
 *  store can skip a render for an empty document.
 *
 * An EMPTY document is always a no-op, whatever `root` says. A genuine subtree
 * publish always enumerates at least its own root row, so zero rows is a
 * fetch gone wrong (a transient error, a truncated response) rather than a
 * completeness claim of "this subtree is now empty" -- and the one thing this
 * module must never do on an ambiguous input is delete a previously known
 * subtree. */
export function mergeSpine(forest: Forest, incoming: readonly AssetNode[], merge: SpineMerge): Forest {
  if (!incoming.length) return forest;
  const nodes = new Map(forest.nodes);
  const origins = new Map(forest.origins);
  let retired = forest.retired;
  const origin: NodeOrigin = { subject: merge.subject, revision: merge.revision };

  if (merge.root !== null) {
    const arriving = new Set<string>();
    for (const n of incoming) arriving.add(n.id);
    const gone: string[] = [];
    for (const [id, was] of origins) {
      if (was.subject !== merge.subject || was.revision === merge.revision || arriving.has(id)) continue;
      gone.push(id);
    }
    if (gone.length) {
      const next = new Map(retired);
      for (const id of gone) {
        next.set(id, nodes.get(id)?.parent ?? null);
        nodes.delete(id);
        origins.delete(id);
      }
      retired = next;
    }
  }

  for (const n of incoming) {
    const prev = nodes.get(n.id);
    // A subtree document names its own top with NO parent -- true of the
    // document, a lie about the forest. Keep the parent a previous slice gave
    // (the same rule ./hierarchy applies to duplicate input rows).
    nodes.set(n.id, prev && n.parent === null && prev.parent !== null ? { ...n, parent: prev.parent } : n);
    origins.set(n.id, origin);
  }
  if (retired.size) {
    // A row that comes back is no longer retired.
    let next: Map<string, string | null> | null = null;
    for (const n of incoming) {
      if (!retired.has(n.id)) continue;
      next ??= new Map(retired);
      next.delete(n.id);
    }
    if (next) retired = next;
  }
  return { nodes, origins, retired };
}
