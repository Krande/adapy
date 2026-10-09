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
  kind: "change-check" | "job";
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
};
