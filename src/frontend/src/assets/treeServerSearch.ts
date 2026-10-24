// The Sources tree's search box, asked of the server too.
//
// The box filters the rows the tree HOLDS, and the tree fetches a level at a time as branches are
// opened -- so a node five levels down in an unopened branch is not found by it. The attribute
// index knows every node of a collection, by name (`name`, a column of the index, not an
// attribute) and source ref, so once the term is long enough the tab also asks the search for the
// collection the tree shows, and lists what it found that the tree has not loaded yet. A click on
// one walks the tree to it the way the search panel's "Show in tree" does (`revealSearchHit`).
//
// WHICH PARTITIONS. The tree shows one collection of this scope, drawn from whichever providers
// published it; each provider's attribute index of it is its own partition, and the index keys
// collections in lower case (a provider writing `AAA` and another `aaa` name one collection). So
// the query names every partition this scope can read whose collection is the tree's, ignoring
// case -- SHARED ONES INCLUDED. A scope often has no index of its own for a collection it shows: its
// tree was requested here, its index lies in the scope it is shared in from. That is safe because a
// hit is revealed by walking its PATH, by name, through the tree on screen (`revealSearchHit`), and
// the providers mint a node's id from its full name, so a shared hit names the same node here.
// Naming them is also what lets the server run a `contains` match at all (it scans).

import { partitionKey } from "@/assets/attributeSearch";
import type { SearchCollection, SearchHit, SearchQuery } from "@/services/api/attributeSearch";

/** How long the term must sit still before the server is asked (after the tree's own filter). */
export const TREE_SERVER_SEARCH_DEBOUNCE_MS = 400;
/** Hits asked for per term: a hint list under the tree, not a result table. */
export const TREE_SERVER_SEARCH_LIMIT = 25;

/** The partition keys the tree's collection is indexed under, as this scope reads them (its own and
 *  shared-in ones alike). */
export function treeSearchPartitions(collections: readonly SearchCollection[], collection: string | null): string[] {
  if (!collection) return [];
  const want = collection.toLowerCase();
  const keys = collections.filter((c) => c.collection.toLowerCase() === want).map(partitionKey);
  return [...new Set(keys)].sort();
}

/** The server query for `term` over `partitions`: name or source ref contains it, any case. Null
 *  when there is nothing to ask (a blank term, no partitions). */
export function treeServerQuery(
  term: string,
  partitions: readonly string[],
  limit = TREE_SERVER_SEARCH_LIMIT,
): SearchQuery | null {
  const t = term.trim();
  if (!t || !partitions.length) return null;
  return {
    providers: null,
    collections: null,
    partitions: [...partitions],
    kind: null,
    depth: null,
    where: [],
    name: { contains: t, ignore_case: true, refs: true },
    select: null,
    limit,
    cursor: null,
  };
}

/** The hits the tree does not hold yet, once each (two providers' partitions can index one node),
 *  in the server's order. `held`: whether the tree has a row of that id. */
export function unloadedHits(hits: readonly SearchHit[], held: (nodeId: string) => boolean): SearchHit[] {
  const seen = new Set<string>();
  const out: SearchHit[] = [];
  for (const h of hits) {
    if (seen.has(h.node_id) || held(h.node_id)) continue;
    seen.add(h.node_id);
    out.push(h);
  }
  return out;
}

/** A hit's full path for one line, root first: its ancestors' names, then its own. */
export function hitPathText(hit: Pick<SearchHit, "full_name" | "path">): string {
  return [...(hit.path ?? []).map((p) => p.full_name), hit.full_name].filter(Boolean).join(" › ");
}

/** A failed server search as one quiet line: a timeout says so; anything else is short. */
export function serverSearchNote(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e);
  if (/\b504\b|did not finish|timeout|timed out/i.test(msg)) return "Unloaded rows: the server search timed out";
  return "Unloaded rows: the server search is unavailable";
}
