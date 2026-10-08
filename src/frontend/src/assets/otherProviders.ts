// "Load this node from another provider": for a row of a model loaded from one provider, the same
// node's geometry from the others that publish it.
//
// Two questions, both answered from what the Sources tab already holds -- no network:
//
//  1. WHICH NODE a Scene row is. A model loaded from a provider is a node of a collection
//     (its source name says which); a row inside it is found in the Sources forest by walking down
//     from that node along the row's names. The model can carry levels the tree does not (a
//     builder's own grouping), so a name with no matching child is stepped over, not a dead end;
//     the row's own name must match.
//  2. WHO ELSE PUBLISHES IT. Providers publishing into one collection share its node ids, so
//     another provider's geometry for the node is its newest manifest at the node or the nearest
//     node above it that delivers geometry.

import { deliversGeometry } from "./geometryMarks";
import type { Forest } from "./merge";
import type { AssetIndex } from "./types";

/** The Sources node a row of a loaded model stands for: walk down from `start` along `names` (the
 *  row's path below the model root, top-down, ending with the row's own name). Null when the
 *  forest does not hold it -- a branch not fetched yet, or a name the tree does not use. */
export function forestNodeFor(forest: Forest, start: string, names: readonly string[]): string | null {
  if (!forest.nodes.has(start)) return null;
  if (!names.length) return start;
  const children = childrenIndex(forest);
  let at = start;
  for (let i = 0; i < names.length; i++) {
    const next = matchChild(forest, children.get(at) ?? [], names[i]);
    if (next) at = next;
    // The row itself must be found; a level above it the tree does not have is stepped over.
    else if (i === names.length - 1) return null;
  }
  return at;
}

let childrenFor: { forest: Forest; map: Map<string, string[]> } | null = null;

function childrenIndex(forest: Forest): Map<string, string[]> {
  if (childrenFor?.forest === forest) return childrenFor.map;
  const map = new Map<string, string[]>();
  for (const n of forest.nodes.values()) {
    if (n.parent == null) continue;
    const list = map.get(n.parent) ?? [];
    list.push(n.id);
    map.set(n.parent, list);
  }
  childrenFor = { forest, map };
  return map;
}

/** A child named `name`: by its label, or by its id where a model disambiguates repeated labels
 *  as `label [id]`. */
function matchChild(forest: Forest, ids: readonly string[], name: string): string | null {
  const tagged = /\[([^\]]+)\]\s*$/.exec(name)?.[1];
  if (tagged && ids.includes(tagged)) return tagged;
  const hits = ids.filter((id) => forest.nodes.get(id)?.label === name);
  return hits.length === 1 ? hits[0] : null;
}

export interface ProviderAlternative {
  readonly provider: string;
  /** Where that provider's claim is: the node itself or the nearest node above it. */
  readonly subject: string;
  readonly revision: string;
  /** The node to load (scoped to it when `subject` is above it). */
  readonly node: string;
}

/** The providers other than `exclude` with geometry for `node` in `collection`, nearest claim
 *  first, one each. */
export function providerAlternatives(
  index: AssetIndex | null,
  forest: Forest,
  collection: string,
  node: string,
  exclude: string,
): ProviderAlternative[] {
  const subjects = index?.collections.get(collection);
  if (!subjects) return [];
  const found = new Map<string, ProviderAlternative>();
  let guard = 0;
  for (let at: string | null = node; at && guard++ < 10_000; at = forest.nodes.get(at)?.parent ?? null) {
    const subject = subjects.get(at);
    if (!subject) continue;
    // Newest first; each provider's newest manifest at this subject decides for it.
    const seen = new Set<string>();
    for (let i = subject.revisions.length - 1; i >= 0; i--) {
      const rev = subject.revisions[i];
      const m = rev.manifest;
      if (!m || seen.has(m.provider)) continue;
      seen.add(m.provider);
      if (m.provider === exclude || found.has(m.provider) || !deliversGeometry(m)) continue;
      found.set(m.provider, { provider: m.provider, subject: at, revision: rev.revision, node });
    }
  }
  return [...found.values()];
}
