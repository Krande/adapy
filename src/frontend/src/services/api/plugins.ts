// Backend plugin routing: the namespaced base URL a plugin builds its own
// endpoints under, ad-hoc plugin job dispatch, and admin scheduling of
// recurring plugin jobs.

import { runtime } from "@/runtime/config";

import { ApiError, authedFetch, jsonOrThrow, readDetail, type ScopeUrl } from "./client";

// One recurring plugin job. Separate from AuditSchedule because the payloads
// differ: an audit sweep names a worker pool and a corpus, a plugin job names a
// plugin and an arbitrary options document that only the plugin understands.
//
// The scheduler tick enqueues these through exactly the path
// ``POST /plugins/{id}/jobs`` uses, so a scheduled firing is indistinguishable
// from a user-initiated one to the worker — and it stamps a timestamp into the
// options on every firing, because core hashes the options into the job's
// source key and byte-identical options would cache-hit the first run forever.
export interface PluginJobSchedule {
  id: string;
  name: string;
  cron_expr: string;
  /** Wire-format scope ("shared", "project:<slug>"), resolved at fire time. */
  scope: string;
  plugin_id: string;
  /** Handed to the plugin verbatim. Core neither validates nor interprets it. */
  options: Record<string, unknown>;
  /** Capability override; null routes to whatever the plugin's live spec says. */
  capability: string | null;
  enabled: boolean;
  last_fired_at: string | null;
  next_fire_at: string | null;
  /** Why the last DUE slot produced no job — an unresolvable scope, a previous
   * run still in flight. Cleared on a successful claim. Without it a skipped
   * schedule is indistinguishable from one that fired and failed elsewhere. */
  last_skipped_reason: string | null;
  last_job_id: string | null;
  created_at: string | null;
  created_by: string | null;
  archived_at: string | null;
}

// One backend plugin the deployment currently offers, as reported by GET
// /plugins: the union of the static built-ins and whatever a live worker
// advertises. A plugin is listed only while a pool providing it is online.
export interface BackendPluginSpec {
  slug: string;
  id?: string;
  title?: string;
  /** Pool tag a job for this plugin is routed to. */
  worker_capability?: string;
  /** Option name whose VALUE shards the capability (`<cap>-<value>`). */
  capability_option?: string;
  /** EFFECTIVE admin gate, not merely what the worker declared. */
  requires_admin?: boolean;
  origin?: string;
  online?: boolean;
}

export const pluginsApi = {
  /** Base URL for a plugin's namespaced REST routes: `/api/plugins/{id}` (the
   * frontend twin of the backend's path-prefixed plugin router convention,
   * Decision 3). A plugin builds its own endpoints as `${pluginBase(id)}/...`;
   * core reserves the `/plugins/` path segment and never names a plugin here. */
  pluginBase(id: string): string {
    return `${runtime.apiBase()}/plugins/${encodeURIComponent(id)}`;
  },

  /** Enqueue an on-demand backend job for a plugin. Generic — core names no
   *  plugin here; `options` is passed opaquely to the plugin's job_entrypoint.
   *
   *  Returns `{job_id, derived_key}` only (NOT a status): poll via
   *  `convertStatus(job_id)` and, on `done`, read the JSON summary with
   *  `getBlob(scope, derived_key)`. Core hashes `options` into the job's
   *  synthetic source key, so an identical repeat request cache-hits a finished
   *  job — add a `refresh` token to the options to deliberately miss that. */
  async pluginJob(
    pluginId: string,
    body: {
      options: Record<string, unknown>;
      derived_key?: string;
      derived_prefix?: string;
      capability?: string;
    },
    opts?: { scope?: ScopeUrl },
  ): Promise<{ job_id: string; derived_key: string }> {
    const base = `${runtime.apiBase()}/plugins/${encodeURIComponent(pluginId)}/jobs`;
    const url = opts?.scope
      ? `${base}?scope=${encodeURIComponent(opts.scope)}`
      : base;
    const r = await authedFetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return jsonOrThrow<{ job_id: string; derived_key: string }>(
      r,
      `pluginJob(${pluginId})`,
    );
  },

  /** The backend plugins this deployment currently offers. Used to suggest a
   * ``plugin_id`` when scheduling one — a suggestion and not a constraint,
   * because the API deliberately accepts an id no live worker serves: a
   * schedule may be created before its worker exists or outlive a pool that is
   * down for a day. */
  async listBackendPlugins(): Promise<{ plugins: BackendPluginSpec[] }> {
    const r = await authedFetch(`${runtime.apiBase()}/plugins`);
    return jsonOrThrow(r, "listBackendPlugins");
  },

  /** Admin: list live plugin-job schedules. Archived rows hidden. */
  async adminPluginJobSchedulesList(): Promise<{
    schedules: PluginJobSchedule[];
  }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/plugin-jobs/schedules`,
    );
    return jsonOrThrow(r, "adminPluginJobSchedulesList");
  },

  /** Admin: create a recurring plugin job. ``cron_expr`` is validated
   * server-side (croniter), and ``options.scheduled_at`` is refused — the tick
   * owns that key. */
  async adminPluginJobScheduleCreate(body: {
    name: string;
    cron_expr: string;
    scope: string;
    plugin_id: string;
    options?: Record<string, unknown>;
    capability?: string | null;
    enabled?: boolean;
  }): Promise<PluginJobSchedule> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/plugin-jobs/schedules`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    return jsonOrThrow(r, "adminPluginJobScheduleCreate");
  },

  /** Admin: partial update — only the keys present are written. Changing
   * ``cron_expr``, or re-enabling a schedule, recomputes ``next_fire_at``
   * server-side so a long-past slot does not fire immediately and then again. */
  async adminPluginJobScheduleUpdate(
    scheduleId: string,
    body: Partial<{
      name: string;
      cron_expr: string;
      scope: string;
      plugin_id: string;
      options: Record<string, unknown>;
      capability: string | null;
      enabled: boolean;
    }>,
  ): Promise<PluginJobSchedule> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/plugin-jobs/schedules/${encodeURIComponent(scheduleId)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    return jsonOrThrow(r, `adminPluginJobScheduleUpdate(${scheduleId})`);
  },

  /** Admin: soft-delete. The tick excludes archived rows, so it stops firing
   * immediately, and the name becomes re-usable. */
  async adminPluginJobScheduleArchive(scheduleId: string): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/plugin-jobs/schedules/${encodeURIComponent(scheduleId)}`,
      { method: "DELETE" },
    );
    if (!r.ok) {
      throw new ApiError(
        `adminPluginJobScheduleArchive(${scheduleId})`,
        r.status,
        await readDetail(r),
      );
    }
  },

  /** Admin: fire a schedule now, without waiting for its slot.
   *
   * This is how a newly created schedule gets verified at all: short of this,
   * an admin has no way to learn whether its options, scope and plugin actually
   * produce a job until the cron comes round. ``next_fire_at`` is left alone,
   * so the scheduled slot still happens. 409 when state said no — the guard
   * against a previous run still being in flight — with the reason in the body. */
  async adminPluginJobScheduleRunNow(scheduleId: string): Promise<{
    job_id: string;
    schedule: string;
    plugin_id: string;
  }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/plugin-jobs/schedules/${encodeURIComponent(scheduleId)}/run`,
      { method: "POST" },
    );
    return jsonOrThrow(r, `adminPluginJobScheduleRunNow(${scheduleId})`);
  },
};
