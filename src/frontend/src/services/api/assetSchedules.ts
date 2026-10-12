// Provider change checks and provider-declared schedules -- the `asset_schedules` key of the
// asset-provider seam (`ada/comms/rest/routes/asset_schedules.py`).
//
//   scope routes  -- the Sources tab: run a check now, list runs, read one run's changed nodes
//   admin routes  -- Admin -> Providers -> Scheduled jobs: every value is a choice the server
//                    offered, and the server re-checks each one; nothing here sends free text.

import { runtime } from "@/runtime/config";

import { authedFetch, jsonOrThrow, type ScopeUrl } from "./client";

export type ChangeAction = "added" | "modified" | "deleted" | "changed" | "unknown";

/** One change-check run (`asset_change_runs`). `status` is `queued` until the job has finished
 *  and the server has copied its `asset_changes` headline in. */
export interface ChangeRun {
  id: string;
  scope: string;
  provider: string;
  collection: string;
  plugin_id: string;
  schedule_id: string | null;
  job_id: string | null;
  requested_by: string | null;
  requested_via: "schedule" | "user";
  status: "queued" | "done" | "error";
  created_at: string | null;
  finished_at: string | null;
  stale: boolean | null;
  up_to_date: boolean | null;
  message: string | null;
  counts: Partial<Record<"added" | "modified" | "deleted" | "unknown", number>> | null;
  users: string[];
  since: string | null;
  checked_at: string | null;
  has_items: boolean;
  error: string | null;
}

/** One changed node (`ada.assets/change-items@1`). `node` is its id in the published tree, or
 *  null for something the provider reports that is not published yet. */
export interface ChangeItem {
  node: string | null;
  action: ChangeAction;
  changed_at: string | null;
  changed_by: string | null;
  name: string | null;
  detail: string | null;
  ref?: string | null;
}

export interface ScheduleChoice {
  value: unknown;
  label: string;
}

export interface ScheduleSetting {
  name: string;
  type: string;
  title: string;
  description: string | null;
  default: unknown;
  source: "enum" | "spec" | "change_users";
  choices?: ScheduleChoice[];
}

export interface ProviderScheduleJob {
  id: string;
  /** `sweep`: every collection the provider offers, one job each, each published
   *  (`routes/asset_sweeps.py`). Its schedule names no collection. */
  kind: "change-check" | "job" | "sweep";
  label: string;
  description: string | null;
  settings: ScheduleSetting[];
}

export interface ProviderSchedules {
  provider: string;
  plugin_id: string;
  collections: string[];
  jobs: ProviderScheduleJob[];
}

export interface ProviderSchedule {
  id: string;
  provider: string;
  job: string;
  kind: string | null;
  scope: string;
  collection: string | null;
  frequency: string | null;
  settings: Record<string, unknown>;
  enabled: boolean;
  declared: boolean;
  last_fired_at: string | null;
  next_fire_at: string | null;
  last_skipped_reason: string | null;
  last_job_id: string | null;
  last_run: ChangeRun | null;
}

export interface LegacyPluginSchedule {
  id: string;
  name: string;
  cron_expr: string;
  scope: string;
  plugin_id: string;
  options: Record<string, unknown>;
  enabled: boolean;
  last_fired_at: string | null;
}

export interface AssetSchedulesListing {
  providers: ProviderSchedules[];
  frequencies: { id: string; label: string; cron: string }[];
  schedules: ProviderSchedule[];
  legacy: LegacyPluginSchedule[];
}

/** What one collection of a sweep took. The provider's own phases (`provider`, e.g. `login_ms`,
 *  `export_ms`, `stage_ms`) and core's publish phases (`publish`: `derive_ms`, `write_ms`, ...) are
 *  as each summary reported them; the rest is wall-clock the API measured. */
export interface SweepItemTimings {
  queued_ms?: number;
  fetch_ms?: number;
  publish_ms?: number;
  total_ms?: number;
  provider?: Record<string, number>;
  login?: { mode?: string };
  publish?: Record<string, number>;
}

export interface SweepItem {
  position: number;
  collection: string;
  /** `excluded`: ticked off -- by the provider's default list or while the sweep ran -- and not run. */
  status: "pending" | "excluded" | "fetching" | "publishing" | "done" | "unchanged" | "nothing" | "error" | "skipped";
  fetch_job_id: string | null;
  publish_job_id: string | null;
  revision: string | null;
  message: string | null;
  error: string | null;
  timings: SweepItemTimings;
  /** Live, for an item in flight only: its job's queue status, stage and progress (0..1). */
  job_status?: string;
  stage?: string | null;
  progress?: number | null;
  started_at: string | null;
  finished_at: string | null;
}

/** One sweep (`asset_sweep_runs`). `items` only on a single run's read; `counts` (items by status)
 *  only on the listing. `interrupted`: the API process driving it is gone. */
export interface SweepRun {
  id: string;
  scope: string;
  provider: string;
  plugin_id: string;
  job: string;
  settings: Record<string, unknown>;
  schedule_id: string | null;
  requested_by: string | null;
  requested_via: "schedule" | "user";
  status: "running" | "done" | "error" | "cancelled" | "interrupted";
  total: number;
  created_at: string | null;
  heartbeat_at: string | null;
  finished_at: string | null;
  error: string | null;
  /** A skip of the item at this position, asked for and not yet carried out. Absent on an older
   *  API. A skipped item ends `skipped` with the message "skipped by <user>". */
  skip_position?: number | null;
  counts?: Partial<Record<SweepItem["status"], number>>;
  items?: SweepItem[];
}

function scopeBase(scope: ScopeUrl): string {
  return `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/asset-changes`;
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export const assetSchedulesApi = {
  /** Run a provider's change check for one collection now. */
  async checkAssetChanges(scope: ScopeUrl, provider: string, collection: string): Promise<ChangeRun> {
    const r = await authedFetch(`${scopeBase(scope)}/check`, json("POST", { provider, collection }));
    return jsonOrThrow(r, "checkAssetChanges");
  },

  /** This scope's change-check runs, newest first. */
  async listAssetChangeRuns(
    scope: ScopeUrl,
    filter: { provider?: string; collection?: string; limit?: number } = {},
  ): Promise<ChangeRun[]> {
    const q = new URLSearchParams();
    if (filter.provider) q.set("provider", filter.provider);
    if (filter.collection) q.set("collection", filter.collection);
    if (filter.limit) q.set("limit", String(filter.limit));
    const r = await authedFetch(`${scopeBase(scope)}/runs${q.toString() ? `?${q}` : ""}`);
    return (await jsonOrThrow<{ runs: ChangeRun[] }>(r, "listAssetChangeRuns")).runs;
  },

  /** One run's changed nodes. */
  async assetChangeRunItems(scope: ScopeUrl, runId: string): Promise<{ run: ChangeRun; items: ChangeItem[] }> {
    const r = await authedFetch(`${scopeBase(scope)}/runs/${encodeURIComponent(runId)}/items`);
    return jsonOrThrow(r, "assetChangeRunItems");
  },

  async adminAssetSchedules(): Promise<AssetSchedulesListing> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/asset-schedules`);
    return jsonOrThrow(r, "adminAssetSchedules");
  },

  /** Each setting's choices for one job, scope and collection. */
  async adminAssetScheduleChoices(
    provider: string,
    job: string,
    scope: string,
    collection: string,
  ): Promise<Record<string, ScheduleChoice[]>> {
    const q = new URLSearchParams({ provider, job, scope, collection });
    const r = await authedFetch(`${runtime.apiBase()}/admin/asset-schedules/choices?${q}`);
    return (await jsonOrThrow<{ settings: Record<string, ScheduleChoice[]> }>(r, "adminAssetScheduleChoices")).settings;
  },

  async adminCreateAssetSchedule(body: {
    provider: string;
    job: string;
    scope: string;
    collection: string | null;
    frequency: string;
    settings: Record<string, unknown>;
    enabled?: boolean;
  }): Promise<unknown> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/asset-schedules`, json("POST", body));
    return jsonOrThrow(r, "adminCreateAssetSchedule");
  },

  async adminUpdateAssetSchedule(
    id: string,
    body: { frequency?: string; settings?: Record<string, unknown>; enabled?: boolean },
  ): Promise<unknown> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/asset-schedules/${encodeURIComponent(id)}`,
      json("PATCH", body),
    );
    return jsonOrThrow(r, "adminUpdateAssetSchedule");
  },

  async adminDeleteAssetSchedule(id: string): Promise<unknown> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/asset-schedules/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
    return jsonOrThrow(r, "adminDeleteAssetSchedule");
  },

  /** Run a provider's sweep once, now -- the same choices a schedule of it is made from. */
  async adminStartAssetSweep(body: {
    provider: string;
    job: string;
    scope: string;
    settings: Record<string, unknown>;
  }): Promise<SweepRun> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/asset-sweeps`, json("POST", body));
    return jsonOrThrow(r, "adminStartAssetSweep");
  },

  async adminAssetSweeps(provider?: string, limit = 10): Promise<SweepRun[]> {
    const q = new URLSearchParams({ limit: String(limit) });
    if (provider) q.set("provider", provider);
    const r = await authedFetch(`${runtime.apiBase()}/admin/asset-sweeps?${q}`);
    return (await jsonOrThrow<{ runs: SweepRun[] }>(r, "adminAssetSweeps")).runs;
  },

  async adminAssetSweep(id: string): Promise<SweepRun> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/asset-sweeps/${encodeURIComponent(id)}`);
    return jsonOrThrow(r, "adminAssetSweep");
  },

  async adminCancelAssetSweep(id: string): Promise<unknown> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/asset-sweeps/${encodeURIComponent(id)}/cancel`,
      json("POST", {}),
    );
    return jsonOrThrow(r, "adminCancelAssetSweep");
  },

  /** Give up on the collection the sweep is on and carry on with the next one. `position` is the
   *  item the caller SAW running: the server refuses (409) when the sweep has moved on since, so a
   *  late click never skips the collection after the one meant. */
  async adminSkipAssetSweepItem(id: string, position: number | null): Promise<{ skip_requested: number }> {
    const q = position == null ? "" : `?position=${position}`;
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/asset-sweeps/${encodeURIComponent(id)}/skip${q}`,
      json("POST", {}),
    );
    return jsonOrThrow(r, "adminSkipAssetSweepItem");
  },

  /** Tick a collection the running sweep has not reached on (`run: true`) or off. 409 once the
   *  sweep has reached it. */
  async adminSetAssetSweepItemRun(id: string, position: number, run: boolean): Promise<{ status: string }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/asset-sweeps/${encodeURIComponent(id)}/items/${position}`,
      json("PATCH", { run }),
    );
    return jsonOrThrow(r, "adminSetAssetSweepItemRun");
  },

  /** A provider's default sweep list: every collection it offers, and which new sweeps leave out. */
  async adminAssetSweepDefaults(provider: string): Promise<SweepDefaults> {
    const q = new URLSearchParams({ provider });
    const r = await authedFetch(`${runtime.apiBase()}/admin/asset-sweeps/defaults?${q}`);
    return jsonOrThrow(r, "adminAssetSweepDefaults");
  },

  async adminSetAssetSweepDefaults(provider: string, excluded: string[]): Promise<SweepDefaults> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/asset-sweeps/defaults`,
      json("PUT", { provider, excluded }),
    );
    return jsonOrThrow(r, "adminSetAssetSweepDefaults");
  },
};

export interface SweepDefaults {
  provider: string;
  /** Present only on the read: every collection the provider offers now (plus any still listed as
   *  excluded that it no longer offers). */
  collections?: string[];
  excluded: string[];
}
