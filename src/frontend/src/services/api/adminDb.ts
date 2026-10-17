// Admin -> Database: what the database holds per feature area, pruning old history, retention,
// and sharing attribute-index partitions into other scopes (routes/admin_db.py; the rules live in
// db/storage_insight.py and db/attribute_index.py).

import { runtime } from "@/runtime/config";

import { authedFetch, jsonOrThrow } from "./client";

export interface DbTableStorage {
  name: string;
  area: string;
  partitioned: boolean;
  /** `reltuples` estimate unless `rows_exact` (small tables are counted). */
  rows: number;
  rows_exact: boolean;
  total_bytes: number;
  table_bytes: number;
  index_bytes: number;
  toast_bytes: number;
  oldest: string | null;
  newest: string | null;
  /** The timestamp the age range reads; null when none is indexed on a large table. */
  age_column: string | null;
  /** A partitioned parent: how many partitions its numbers sum. */
  partitions?: number;
  /** An attribute-index table not attached to the index (an ingest building it, or a dead one). */
  detached?: boolean;
}

export interface DbAreaStorage {
  id: string;
  label: string;
  description: string;
  /** `age`: older than N days; `collections`: whole attribute-index collections; null: not here. */
  prune: "age" | "collections" | null;
  prune_note: string | null;
  rows: number;
  total_bytes: number;
  table_bytes: number;
  index_bytes: number;
  toast_bytes: number;
  oldest: string | null;
  newest: string | null;
  tables: DbTableStorage[];
}

export interface DbAttributePartition {
  name: string;
  parent: string;
  scope: string | null;
  provider: string | null;
  collection: string | null;
  revision: string | null;
  rows: number;
  rows_exact: boolean;
  indexed_rows: number | null;
  ingested_at: string | null;
  total_bytes: number;
  table_bytes: number;
  index_bytes: number;
  toast_bytes: number;
  /** The scopes (storage prefixes) this partition is shared into, oldest share first (absent from
   *  a server from before shares). */
  shared_with?: string[];
  /** The scopes its whole (scope, provider) group is shared into (migration 040): it is searched
   *  there too, whether or not it has a share of its own. */
  shared_with_group?: string[];
}

/** A whole (scope, provider) group made searchable from another scope, later collections included. */
export interface DbAttrProviderShare {
  scope: string;
  provider: string;
  target_scope: string;
  shared_at: string | null;
  shared_by: string | null;
}

/** One copy of a collection held in several scopes. */
export interface DbDuplicateMember {
  scope: string;
  revision: string | null;
  rows: number;
  bytes: number;
  ingested_at: string | null;
}

/** One provider's collections held by both of two scopes, summed. */
export interface DbDuplicateGroup {
  provider: string;
  /** The shared scope first when it is one of them. */
  scopes: [string, string];
  /** `shared` when one side is the shared scope -- the only copy a fold may keep. */
  keep_scope: string | null;
  drop_scope: string | null;
  collections: number;
  same_revision: number;
  /** Per scope: how many of the differing copies it holds the later ingest of. */
  newer_ingest: Record<string, number>;
  bytes: Record<string, number>;
  rows: Record<string, number>;
  /** What dropping `drop_scope`'s copies frees; null when no side is shared. */
  reclaimable_bytes: number | null;
}

export interface DbAttributeDuplicates {
  groups: DbDuplicateGroup[];
  collections: { provider: string; collection: string; members: DbDuplicateMember[] }[];
}

export interface DbFoldResult {
  dry_run: boolean;
  provider: string;
  keep_scope: string;
  drop_scope: string;
  collections: {
    collection: string;
    keep_revision: string | null;
    drop_revision: string | null;
    same_revision: boolean;
    rows: number;
    bytes: number;
    repointed: string[];
  }[];
  folded: number;
  bytes: number;
  missing: string[];
  errors: string[];
}

export interface DbPruneRevisionsSpec {
  provider?: string;
  scopes?: string[];
  collections?: string[];
  include_narrowing?: boolean;
}

type DbRevisionCopy = DbDuplicateMember & { action?: "fold" | "drop"; reason?: string; note?: string; repointed?: string[] };

export interface DbPruneRevisionsResult {
  dry_run: boolean;
  include_narrowing: boolean;
  collections: { provider: string; collection: string; kept: DbRevisionCopy; dropped: DbRevisionCopy[]; skipped: DbRevisionCopy[] }[];
  dropped: number;
  skipped: number;
  bytes: number;
  errors: string[];
}

/** One partition made searchable from another scope too. Scopes are storage prefixes. */
export interface DbAttrShare {
  scope: string;
  provider: string;
  collection: string;
  target_scope: string;
  shared_at: string | null;
  shared_by: string | null;
}

export interface DbRetentionLastRun {
  at: string;
  result?: { rows: number; est_bytes: number; errors?: string[]; areas?: Record<string, { rows: number; est_bytes: number }> };
}

export interface DbStorageReport {
  database: { name: string; size_bytes: number; tables_bytes: number };
  areas: DbAreaStorage[];
  attribute_partitions: DbAttributePartition[];
  /** Absent from a server from before group shares / duplicate reporting. */
  attribute_provider_shares?: DbAttrProviderShare[];
  attribute_duplicates?: DbAttributeDuplicates;
  retention: Record<string, number>;
  retention_last_run: DbRetentionLastRun | null;
  took_ms: number;
}

export interface DbCollectionRef {
  scope: string;
  provider: string;
  collection: string;
}

export type DbPruneSpec = { older_than_days: number } | { collections: DbCollectionRef[] };

export interface DbPruneArea {
  id: string;
  older_than_days: number | null;
  cutoff: string | null;
  rows: number;
  est_bytes: number;
  /** Old enough, but in flight (a running sweep, a queued check, ...): never deleted. */
  protected: number;
  tables: { table: string; rows: number; est_bytes: number; note?: string }[];
  collections?: (DbCollectionRef & { rows: number; ingested_at: string | null; partition: string; est_bytes: number; dropped?: boolean })[];
  errors?: string[];
}

export interface DbPruneResult {
  dry_run: boolean;
  areas: DbPruneArea[];
  rows: number;
  est_bytes: number;
  vacuum: { ran: string[]; errors: string[] };
  note: string;
  took_ms: number;
}

export interface DbTableColumn {
  name: string;
  /** As Postgres spells it (`format_type`), e.g. `timestamp with time zone`. */
  type: string;
  /** A secret-looking column: its cells read "<redacted>" (or null when unset). */
  redacted: boolean;
}

/** A cell as the API bounds it: timestamps ISO, `bytea` as "<N bytes>", long text cut with a
 *  "…[truncated]" marker, huge integers as text. */
export type DbCell = string | number | boolean | null;

export interface DbTableRows {
  table: string;
  columns: DbTableColumn[];
  /** The primary key's columns, or `["ctid"]` (physical order). */
  order_by: string[];
  rows: DbCell[][];
  offset: number;
  limit: number;
  /** The planner's estimate unless `total_exact` (small tables are counted). */
  total: number;
  total_exact: boolean;
  took_ms: number;
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export const adminDbApi = {
  async adminDbStorage(): Promise<DbStorageReport> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/db/storage`);
    return jsonOrThrow(r, "adminDbStorage");
  },

  /** One page (limit <= 200) of a table the storage report lists; 404 for any other name. */
  async adminDbTableRows(table: string, offset: number, limit: number): Promise<DbTableRows> {
    const q = new URLSearchParams({ offset: String(offset), limit: String(limit) });
    const r = await authedFetch(`${runtime.apiBase()}/admin/db/tables/${encodeURIComponent(table)}/rows?${q}`);
    return jsonOrThrow(r, "adminDbTableRows");
  },

  /** A dry run unless `dryRun` is false. */
  async adminDbPrune(areas: Record<string, DbPruneSpec>, dryRun: boolean): Promise<DbPruneResult> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/db/prune`, json("POST", { areas, dry_run: dryRun }));
    return jsonOrThrow(r, "adminDbPrune");
  },

  /** Set (days) or clear (null) each named area's retention. */
  async adminDbSetRetention(
    retention: Record<string, number | null>,
  ): Promise<{ retention: Record<string, number>; last_run: DbRetentionLastRun | null }> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/db/retention`, json("PUT", { retention }));
    return jsonOrThrow(r, "adminDbSetRetention");
  },

  /** Make one attribute-index partition searchable from `targetScope` too (idempotent). A share
   *  grants nobody access: a search reads it only for callers who can reach its origin scope. */
  async adminDbShareAttrCollection(ref: DbCollectionRef, targetScope: string): Promise<DbAttrShare> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/db/attr-shares`, json("POST", { ...ref, target_scope: targetScope }));
    return (await jsonOrThrow<{ share: DbAttrShare }>(r, "adminDbShareAttrCollection")).share;
  },

  async adminDbUnshareAttrCollection(ref: DbCollectionRef, targetScope: string): Promise<boolean> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/db/attr-shares`, json("DELETE", { ...ref, target_scope: targetScope }));
    return (await jsonOrThrow<{ removed: boolean }>(r, "adminDbUnshareAttrCollection")).removed;
  },

  /** Share every partition of (scope, provider) -- later ones too -- into `targetScope`. */
  async adminDbShareAttrProvider(scope: string, provider: string, targetScope: string): Promise<DbAttrProviderShare> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/db/attr-provider-shares`,
      json("POST", { scope, provider, target_scope: targetScope }),
    );
    return (await jsonOrThrow<{ share: DbAttrProviderShare }>(r, "adminDbShareAttrProvider")).share;
  },

  /** Stop the group share; the collections' own shares stay. */
  async adminDbUnshareAttrProvider(scope: string, provider: string, targetScope: string): Promise<boolean> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/db/attr-provider-shares`,
      json("DELETE", { scope, provider, target_scope: targetScope }),
    );
    return (await jsonOrThrow<{ removed: boolean }>(r, "adminDbUnshareAttrProvider")).removed;
  },

  /** Keep the shared copy of each collection `provider` holds in both scopes; a dry run unless
   *  `dryRun` is false. */
  async adminDbFoldAttr(provider: string, keepScope: string, dropScope: string, dryRun: boolean): Promise<DbFoldResult> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/db/attr-fold`,
      json("POST", { provider, keep_scope: keepScope, drop_scope: dropScope, dry_run: dryRun }),
    );
    return jsonOrThrow(r, "adminDbFoldAttr");
  },

  /** Keep only the newest copy of each duplicated collection; a dry run unless `dryRun` is false. */
  async adminDbPruneAttrRevisions(spec: DbPruneRevisionsSpec, dryRun: boolean): Promise<DbPruneRevisionsResult> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/db/attr-prune-revisions`, json("POST", { ...spec, dry_run: dryRun }));
    return jsonOrThrow(r, "adminDbPruneAttrRevisions");
  },
};
