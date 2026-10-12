// Admin -> Database: what the database holds per feature area, pruning old history, retention
// (routes/admin_db.py; the rules live in db/storage_insight.py).

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
}

export interface DbRetentionLastRun {
  at: string;
  result?: { rows: number; est_bytes: number; errors?: string[]; areas?: Record<string, { rows: number; est_bytes: number }> };
}

export interface DbStorageReport {
  database: { name: string; size_bytes: number; tables_bytes: number };
  areas: DbAreaStorage[];
  attribute_partitions: DbAttributePartition[];
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
};
