// Metrics: client-recorded view-load/render-profile samples, plus the
// admin perf report/hotspots/thresholds and profile-stats views built on
// top of them.

import { runtime } from "@/runtime/config";

import {
  ApiError,
  authedFetch,
  jsonOrThrow,
  readDetail,
  toQueryString,
  type ScopeUrl,
} from "./client";

// One row in the cross-conversion perf table (M6). ``streaming``
// is the classifier's verdict; ``signals`` lists the threshold keys
// that fired so the UI can render specific reasons in a tooltip.
export interface PerfCell {
  source_ext: string;
  target_format: string;
  sample_count: number;
  fail_count: number;
  ok_count: number;
  failure_rate: number;
  duration_ms_p50: number | null;
  duration_ms_p95: number | null;
  duration_ms_max: number | null;
  peak_rss_kb_p50: number | null;
  peak_rss_kb_p95: number | null;
  peak_rss_max_kb: number | null;
  peak_rss_per_source_mb_p95: number | null;
  write_bytes_p50: number | null;
  write_bytes_p95: number | null;
  read_bytes_avg: number | null;
  // Fraction of wall-clock spent in CPU (user + sys) across all
  // samples. Null when no rows had non-null duration. Below the
  // ``cpu_fraction_max`` threshold the classifier flags the cell
  // as IO-bound — see ``streaming.signals`` for the firing list.
  cpu_fraction: number | null;
  streaming: { is_candidate: boolean; signals: string[] };
}

// One aggregated hot function inside a cell. ``agg_cumtime`` is the
// SUM of pstats' ``cumtime`` across every profiled run in the
// window — total seconds the function and its callees consumed.
export interface PerfHotspotRow {
  func: string;
  file: string;
  line: number;
  agg_cumtime: number;
  agg_ncalls: number;
  profiles_seen: number;
}

export interface PerfHotspotsResp {
  source_ext: string | null;
  target_format: string | null;
  functions: PerfHotspotRow[];
  profiles_in_window: number;
  total_top_cumtime_in_window: number;
  since_days: number;
}

export interface PerfReport {
  cells: PerfCell[];
  thresholds: Record<string, number>;
  signal_reasons: Record<string, string>;
  since_days: number;
  trigger: "all" | "audit" | "user";
  audit_run_id: string | null;
  worker_image_tag: string | null;
  generated_at: string;
}

export interface PerfThresholdsResp {
  thresholds: Record<string, number>;
  defaults: Record<string, number>;
}

export interface ProfileStatsRow {
  func: string;
  file: string;
  line: number;
  ncalls: number;
  primitive_calls: number;
  tottime: number;
  percall_tot: number;
  cumtime: number;
  percall_cum: number;
}

export interface ProfileStatsResp {
  audit_id: number;
  total_tottime: number;
  row_count: number;
  rows: ProfileStatsRow[];
}

export interface MetricsSample {
  ts: number; // epoch seconds
  elapsed_s: number; // seconds since job start
  cpu_user_ms: number;
  cpu_sys_ms: number;
  rss_kb: number;
  peak_rss_kb: number;
  read_bytes: number;
  write_bytes: number;
  // Per-thread cumulative CPU (utime+stime, ms) keyed by tid — drives the per-core utilization
  // envelope for the in-process native engine. Absent on older rows / non-native conversions.
  per_thread_cpu_ms?: Record<string, number> | null;
}

export interface MetricsHistoryResp {
  audit_id: number;
  samples: MetricsSample[];
}

export const metricsApi = {
  /** Admin: cross-conversion perf snapshot (M6). Aggregates the
   * last ``since`` days of convert jobs into a per (source × target)
   * cell table with p50 / p95 / max metrics + a streaming-candidate
   * verdict on each cell. ``audit_run_id`` + ``worker_image_tag``
   * narrow the snapshot to one sweep / one worker build so old or
   * cached data from a different image doesn't dilute it. */
  async adminPerfReport(opts?: {
    since?: number;
    trigger?: "all" | "audit" | "user";
    audit_run_id?: string;
    worker_image_tag?: string;
  }): Promise<PerfReport> {
    const params = new URLSearchParams();
    if (opts?.since != null) params.set("since", String(opts.since));
    if (opts?.trigger) params.set("trigger", opts.trigger);
    if (opts?.audit_run_id) params.set("audit_run_id", opts.audit_run_id);
    if (opts?.worker_image_tag)
      params.set("worker_image_tag", opts.worker_image_tag);
    const qs = toQueryString(params);
    const url = `${runtime.apiBase()}/admin/audit/perf${qs ? `?${qs}` : ""}`;
    const r = await authedFetch(url);
    return jsonOrThrow(r, "adminPerfReport");
  },

  /** Admin: distinct worker_image_tag values seen in the perf
   * window, freshest first. Drives the PerformanceTab "Worker SHA"
   * picker — only tags with data behind them appear. */
  async adminPerfWorkers(since = 90): Promise<{
    workers: { tag: string; samples: number; last_seen: string | null }[];
    since_days: number;
  }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/perf/workers?since=${since}`,
    );
    return jsonOrThrow(r, "adminPerfWorkers");
  },

  /** Admin: effective streaming-classifier thresholds, plus the
   * shipped defaults so the UI can label overridden rows. */
  async adminPerfThresholdsGet(): Promise<PerfThresholdsResp> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/perf/thresholds`,
    );
    return jsonOrThrow(r, "adminPerfThresholdsGet");
  },

  /** Admin: write threshold overrides. Pass ``null`` for a key to
   * clear the override (ship-default takes over). Unknown keys
   * 400 — we'd rather catch a typo than silently disable a signal. */
  async adminPerfThresholdsSet(
    body: Record<string, number | null>,
  ): Promise<PerfThresholdsResp> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/perf/thresholds`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    return jsonOrThrow(r, "adminPerfThresholdsSet");
  },

  /** Admin: function-level hotspots aggregated across recent
   * profiles in one cell. Empty ``functions`` + ``profiles_in_window=0``
   * usually means ``profile_conversions`` was off during the
   * window, or the background parser hasn't caught up yet. */
  async adminPerfHotspots(opts: {
    source_ext?: string;
    target_format?: string;
    since?: number;
    limit?: number;
  }): Promise<PerfHotspotsResp> {
    const params = new URLSearchParams();
    if (opts.source_ext) params.set("source_ext", opts.source_ext);
    if (opts.target_format) params.set("target_format", opts.target_format);
    if (opts.since != null) params.set("since", String(opts.since));
    if (opts.limit != null) params.set("limit", String(opts.limit));
    const qs = toQueryString(params);
    const url = `${runtime.apiBase()}/admin/audit/perf/hotspots${qs ? `?${qs}` : ""}`;
    const r = await authedFetch(url);
    return jsonOrThrow(r, "adminPerfHotspots");
  },

  /** Record one browser model-load (``action='view'``) — the viewer's
   * opt-in load instrumentation posts this once a GLB is in the scene.
   * Best-effort: never throws into the load path. ``client_metrics`` is
   * the per-phase IO/network/CPU/GPU + payload + device breakdown. */
  async recordViewLoad(
    scope: ScopeUrl,
    body: {
      key: string;
      status?: "ok" | "error";
      duration_ms?: number | null;
      read_bytes?: number | null;
      write_bytes?: number | null;
      peak_rss_kb?: number | null;
      error?: string | null;
      traceback?: string | null;
      client_metrics?: Record<string, unknown> | null;
    },
  ): Promise<void> {
    try {
      await authedFetch(
        `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/audit/view`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        },
      );
    } catch (e) {
      // Metrics must never disrupt the session.
      console.debug("recordViewLoad failed (ignored)", e);
    }
  },

  /** Record one steady-state render-performance window
   * (``action='render'``). Same best-effort contract as
   * ``recordViewLoad``. */
  async recordRenderProfile(
    scope: ScopeUrl,
    body: {
      key: string;
      duration_ms?: number | null;
      client_metrics?: Record<string, unknown> | null;
    },
  ): Promise<void> {
    try {
      await authedFetch(
        `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/audit/view`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          // The ingest endpoint stores any action via client_metrics;
          // render rows are marked by client_metrics.kind === "render"
          // and the backend routes them to action='render'.
          body: JSON.stringify(body),
        },
      );
    } catch (e) {
      console.debug("recordRenderProfile failed (ignored)", e);
    }
  },

  /** Admin: per-file browser model-load perf snapshot. One cell per
   * GLB with p50/p95 of every load phase + a dominant-bottleneck
   * label (io / network / cpu / gpu). */
  async adminFrontendLoads(since = 30): Promise<{
    cells: Array<Record<string, number | string | null>>;
    since_days: number;
    generated_at: string;
  }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/frontend-loads?since=${since}`,
    );
    return jsonOrThrow(r, "adminFrontendLoads");
  },

  /** Admin: function-level hotspots (summed JS Self-Profiling self-time
   * per TS/WASM frame) across browser loads, optionally one ``key``. */
  async adminFrontendLoadHotspots(opts: {
    key?: string;
    since?: number;
    limit?: number;
    kind?: "view" | "render";
  }): Promise<{
    functions: Array<{
      fn: string;
      samples: number;
      self_ms_sum: number | null;
      self_ms_avg: number | null;
      total_ms_max: number | null;
      is_wasm: boolean;
    }>;
    loads_in_window: number;
    key: string | null;
    kind: string;
    since_days: number;
  }> {
    const params = new URLSearchParams();
    if (opts.key) params.set("key", opts.key);
    if (opts.since != null) params.set("since", String(opts.since));
    if (opts.limit != null) params.set("limit", String(opts.limit));
    if (opts.kind) params.set("kind", opts.kind);
    const qs = toQueryString(params);
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/frontend-loads/hotspots${qs ? `?${qs}` : ""}`,
    );
    return jsonOrThrow(r, "adminFrontendLoadHotspots");
  },

  /** Admin: per-file steady-state render-performance snapshot
   * (``action='render'``). */
  async adminRenderProfiles(since = 30): Promise<{
    cells: Array<Record<string, number | string | null>>;
    since_days: number;
    generated_at: string;
  }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/render?since=${since}`,
    );
    return jsonOrThrow(r, "adminRenderProfiles");
  },

  /** Direct URL for a profile-dump download. Auth-aware caller
   * should fetch via authedFetch + blob — exposing the URL here
   * keeps it composable with the table's <a download>. */
  adminProfileUrl(auditId: number): string {
    return `${runtime.apiBase()}/admin/audit/${auditId}/profile`;
  },

  /** Trigger the .prof download with the bearer token attached. */
  async adminDownloadProfile(
    auditId: number,
    suggestedName: string,
  ): Promise<void> {
    const r = await authedFetch(this.adminProfileUrl(auditId));
    if (!r.ok) {
      throw new ApiError(
        `adminDownloadProfile(${auditId})`,
        r.status,
        await readDetail(r),
      );
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    try {
      const a = document.createElement("a");
      a.href = url;
      a.download = suggestedName;
      a.style.display = "none";
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    } finally {
      URL.revokeObjectURL(url);
    }
  },

  /** Server-parsed profile stats for the dashboard table.
   * Returns one row per function with cumtime / tottime / call counts;
   * the SPA sorts client-side so the user can pivot freely. */
  async adminProfileStats(
    auditId: number,
    limit = 500,
  ): Promise<ProfileStatsResp> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/${auditId}/profile/stats?limit=${limit}`,
    );
    return jsonOrThrow(r, `adminProfileStats(${auditId})`);
  },

  /** Per-heartbeat resource samples (RSS / CPU / IO) captured by the
   * worker subprocess wrapper while the convert child was alive. */
  async adminMetricsHistory(auditId: number): Promise<MetricsHistoryResp> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/${auditId}/metrics-history`,
    );
    return jsonOrThrow(r, `adminMetricsHistory(${auditId})`);
  },

  /** Admin: clear all conversion metrics + delete profile blobs.
   * Returns counts so the UI can confirm what was wiped. */
  async adminClearMetrics(): Promise<{
    rows_cleared: number;
    profiles_deleted: number;
    errors: string[];
  }> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/audit/metrics`, {
      method: "DELETE",
    });
    return jsonOrThrow(r, "adminClearMetrics");
  },
};
