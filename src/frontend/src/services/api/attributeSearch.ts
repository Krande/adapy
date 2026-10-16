// Attribute search over the scope's attribute index -- the `ada.assets/attribute-index@1` rows a
// provider stages beside (or instead of) its tree, ingested by core into one partition per
// (provider, collection) of this scope (`ada/comms/rest/routes/assets.py`, `/assets/search*`).
//
//   search       POST  one page of matching nodes, every hit with its path root-first, and the
//                      total per partition for the WHOLE query (not just the page)
//   collections  GET   what has been ingested in this scope, one entry per (provider, collection)
//   keys         GET   the attribute keys present, sorted -- what a filter row's key offers
//   values       GET   a key's values with counts, optionally by prefix -- what a value offers
//
// Provider-neutral on purpose: keys, kinds and values are whatever the provider wrote, case kept.
// Nothing here knows one provider's vocabulary from another's.
//
// SHARED PARTITIONS. An admin can share one scope's partition into another (Admin -> Database);
// a search of the target scope then reads it where it lies, beside the scope's own -- for a caller
// who can reach the scope it came from. So a hit or a collection says its ORIGIN (`scope`, the
// storage prefix; `scope_wire`, the path segment naming it to this caller, null when there is
// none; `shared`). All three are optional: a server from before shares sends none, and then
// everything is the scope's own.

import { runtime } from "@/runtime/config";

import { authedFetch, jsonOrThrow, type ScopeUrl } from "./client";

/** One filter on an attribute: exactly one operator beside `key`. `ne` / `not_in` are the exact
 *  complement of `eq` / `in` -- a node without the key matches them too; `exists` / `missing` test
 *  the key alone. A query of only negative / presence filters cannot use the server's index (it
 *  scans every chosen collection, under the search's statement timeout). */
export type SearchWhere =
  | { key: string; eq: string | number | boolean }
  | { key: string; ne: string | number | boolean }
  | { key: string; in: (string | number | boolean)[] }
  | { key: string; not_in: (string | number | boolean)[] }
  | { key: string; prefix: string }
  | { key: string; exists: true }
  | { key: string; missing: true };

export type SearchDepth = { eq: number } | { min?: number; max?: number };

/** The search body (contract `POST /api/scopes/{scope}/assets/search`). `null` = no constraint;
 *  `select: null` = every attribute of each hit. The `exclude_*` lists leave those providers /
 *  collections out whatever the includes say (optional: a query stored before they existed has
 *  none). */
export interface SearchQuery {
  providers: string[] | null;
  collections: string[] | null;
  exclude_providers?: string[] | null;
  exclude_collections?: string[] | null;
  kind: string[] | null;
  depth: SearchDepth | null;
  where: SearchWhere[];
  select: string[] | null;
  limit: number;
  cursor: string | null;
}

/** One ancestor on a hit's path, root first. */
export interface SearchPathEntry {
  node_id: string;
  full_name: string;
  kind: string | null;
  depth: number | null;
}

/** Where a partition lies (see the module comment). */
export interface SearchOrigin {
  /** The origin scope's storage prefix: `shared`, `projects/<id>`, `users/<sub>`. */
  scope?: string;
  /** The origin as an API path segment (`project:<id>`, `user:me`, ...), null when it has none. */
  scope_wire?: string | null;
  /** Read through a share from another scope. */
  shared?: boolean;
}

/** One matching node. `node_id` is the id the provider's published tree uses for it, which is what
 *  lets a hit be revealed in the Sources tree -- the ORIGIN scope's tree, for a shared hit. */
export interface SearchHit extends SearchOrigin {
  provider: string;
  collection: string;
  node_id: string;
  parent_id: string | null;
  full_name: string;
  source_ref: string | null;
  kind: string | null;
  depth: number | null;
  attrs: Record<string, string | number | boolean>;
  path: SearchPathEntry[];
}

/** One partition's total for the whole query, its `counts` key spelled out. */
export interface SearchPartition extends SearchOrigin {
  key: string;
  provider: string;
  collection: string;
  count: number;
}

export interface SearchResponse {
  rows: SearchHit[];
  next_cursor: string | null;
  /** Partition -> total matches in it for the whole query. Keyed `<provider>/<collection>` for the
   *  scope's own, `<provider>/<collection>@<origin scope>` for a shared one; read `partitions`
   *  rather than splitting the keys. */
  counts: Record<string, number>;
  /** `counts` spelled out (absent from a server from before shares). */
  partitions?: SearchPartition[];
  took_ms: number;
}

export interface SearchCollection extends SearchOrigin {
  provider: string;
  collection: string;
  revision: string | null;
  rows: number;
  source_etag: string | null;
  ingested_at: string | null;
}

export interface SearchValueCount {
  value: string | number | boolean;
  count: number;
}

/** Narrow a key/value lookup to one partition; both optional. */
export interface SearchPartitionFilter {
  provider?: string | null;
  collection?: string | null;
}

function searchBase(scope: ScopeUrl): string {
  return `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/assets/search`;
}

function partitionParams(q: URLSearchParams, filter: SearchPartitionFilter): URLSearchParams {
  if (filter.provider) q.set("provider", filter.provider);
  if (filter.collection) q.set("collection", filter.collection);
  return q;
}

export const attributeSearchApi = {
  /** One page of hits. Pass the previous page's `next_cursor` (same query otherwise) for the next. */
  async searchAssetAttributes(scope: ScopeUrl, query: SearchQuery, signal?: AbortSignal): Promise<SearchResponse> {
    const r = await authedFetch(searchBase(scope), {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(query),
      signal,
    });
    return jsonOrThrow(r, "searchAssetAttributes");
  },

  /** Every ingested (provider, collection) in this scope. */
  async searchCollections(scope: ScopeUrl): Promise<SearchCollection[]> {
    const r = await authedFetch(`${searchBase(scope)}/collections`);
    return (await jsonOrThrow<{ collections: SearchCollection[] }>(r, "searchCollections")).collections;
  },

  /** The attribute keys present (sorted), in the scope or one partition of it. */
  async searchKeys(scope: ScopeUrl, filter: SearchPartitionFilter = {}): Promise<string[]> {
    const q = partitionParams(new URLSearchParams(), filter);
    const r = await authedFetch(`${searchBase(scope)}/keys${q.toString() ? `?${q}` : ""}`);
    return (await jsonOrThrow<{ keys: string[] }>(r, "searchKeys")).keys;
  },

  /** One key's values with their counts, those starting with `prefix` when given. */
  async searchValues(
    scope: ScopeUrl,
    key: string,
    opts: SearchPartitionFilter & { prefix?: string; limit?: number } = {},
    signal?: AbortSignal,
  ): Promise<SearchValueCount[]> {
    const q = partitionParams(new URLSearchParams({ key }), opts);
    if (opts.prefix) q.set("prefix", opts.prefix);
    if (opts.limit) q.set("limit", String(opts.limit));
    const r = await authedFetch(`${searchBase(scope)}/values?${q}`, { signal });
    return (await jsonOrThrow<{ values: SearchValueCount[] }>(r, "searchValues")).values;
  },
};
