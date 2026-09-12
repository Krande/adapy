// Conversion jobs: enqueue/poll a server-side convert, the caller's own job
// list, and client-computed (local/WASM) audit-run job bookkeeping.

import { runtime } from "@/runtime/config";

import {
  ApiError,
  authedFetch,
  jsonOrThrow,
  readDetail,
  type ConvertStatus,
  type ScopeUrl,
  type TargetFormat,
} from "./client";
import type { AuditEntry } from "./types/audit";

export interface ConvertResponse {
  job_id: string;
  source_key: string;
  derived_key: string;
  target_format?: TargetFormat;
  status: ConvertStatus;
  progress: number;
  stage: string;
  error: string | null;
  cached: boolean;
  scope_kind?: string;
  scope_id?: string | null;
  /** The worker pool this job was routed to, as a NATS subject token.
   *
   * The API returns the whole queue record, and this field is the one that
   * explains a job nothing picks up: a pool no worker subscribes to accepts the
   * job and then never delivers it, so there is no error, no retry and no worker
   * log line — only a row that stays `queued`. */
  target_capability?: string | null;
}

export interface ConvertTargetsResponse {
  source_key: string;
  targets: TargetFormat[];
}

export const conversionApi = {
  /** Open an audit row for an in-browser (WASM) conversion. Returns the
   * server-assigned ``wasm-<uuid>`` job id to pass to auditLocalUpdate.
   * ``auditRunId`` attaches the row to an admin audit-run sweep. */
  async auditLocalCreate(
    scope: ScopeUrl,
    body: {
      key: string;
      target_format: string;
      audit_run_id?: string | null;
      image_tag?: string;
    },
  ): Promise<string> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/audit/local`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    const j = await jsonOrThrow<{ job_id: string }>(r, "auditLocalCreate");
    return j.job_id;
  },

  /** Patch a WASM conversion's audit row to its terminal outcome with
   * captured metrics. Best-effort at the call site — a lost audit
   * update must never fail the conversion. */
  async auditLocalUpdate(
    scope: ScopeUrl,
    jobId: string,
    body: {
      status: "done" | "ok" | "error" | "skipped" | "cancelled";
      duration_ms?: number;
      read_bytes?: number;
      write_bytes?: number;
      peak_rss_kb?: number;
      error?: string | null;
      traceback?: string | null;
      metrics_samples?: Array<Record<string, number>>;
    },
  ): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/audit/local/${encodeURIComponent(jobId)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    if (!r.ok) {
      throw new ApiError(
        `auditLocalUpdate(${jobId})`,
        r.status,
        await readDetail(r),
      );
    }
  },

  /** Enqueue a server-side conversion. Returns either a fresh queued
   * job, a synthesised "cached" response (derived already present),
   * or rejects with ApiError. ``step`` and ``field`` only apply to
   * FEA result sources (.sif) — set both to override the default
   * field selection, or leave both undefined for the auto pick. */
  async convert(
    scope: ScopeUrl,
    sourceKey: string,
    targetFormat: TargetFormat = "glb",
    opts?: {
      step?: number;
      field?: string;
      // Per-job knobs. Keys come from the conversion matrix's
      // ``options[<target>]`` schema (declared at the worker
      // ``@converter(options=...)`` site) plus the legacy
      // hardcoded set (use_sat_pcurves / skip_shapefix /
      // profile_conversions) that still ride
      // the env-var rail. Values are tri-state native:
      // ``null`` clears any global override; otherwise the
      // type matches the option's declared ``type``.
      conversionOptions?: Record<string, boolean | string | number | null>;
      // Re-convert: always re-run and write to the separate ``_reconvert/`` namespace so
      // a corpus scope's ``_derived/`` audit product is never overwritten.
      reconvert?: boolean;
    },
  ): Promise<ConvertResponse> {
    const body: Record<string, unknown> = {
      source_key: sourceKey,
      target_format: targetFormat,
    };
    if (opts?.step !== undefined && opts?.field !== undefined) {
      body.step = opts.step;
      body.field = opts.field;
    }
    if (opts?.conversionOptions && Object.keys(opts.conversionOptions).length) {
      body.conversion_options = opts.conversionOptions;
    }
    if (opts?.reconvert) {
      body.reconvert = true;
    }
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/convert`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    return jsonOrThrow<ConvertResponse>(
      r,
      `convert(${sourceKey} -> ${targetFormat})`,
    );
  },

  /** Poll a single conversion job by id. Job_id is globally unique,
   * so the URL doesn't carry a scope — the server re-checks access
   * against the scope recorded on the job. */
  async convertStatus(jobId: string): Promise<ConvertResponse> {
    const r = await authedFetch(
      `${runtime.apiBase()}/convert/${encodeURIComponent(jobId)}`,
    );
    return jsonOrThrow<ConvertResponse>(r, `convertStatus(${jobId})`);
  },

  /** Enqueue a worker utility against a loaded scene model. Returns the job
   * (poll via ``convertStatus``; on ``done`` fetch ``derived_key`` for the
   * viewer-ops JSON). Mirrors :func:`convert` but hits the utility endpoint. */
  async runUtility(
    scope: ScopeUrl,
    sourceKey: string,
    utilityName: string,
    kwargs: Record<string, boolean | string | number | null>,
  ): Promise<ConvertResponse> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/utility`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          source_key: sourceKey,
          utility_name: utilityName,
          kwargs,
        }),
      },
    );
    return jsonOrThrow<ConvertResponse>(
      r,
      `runUtility(${utilityName} on ${sourceKey})`,
    );
  },

  /** In-flight conversions the current user started in this scope.
   *  Used by the bottom-right toast to repopulate on page reload so
   *  a long bake the user kicked off and walked away from still
   *  shows up when they come back. Errors are intentionally
   *  excluded — they're terminal and the toast's error row expects
   *  manual dismissal, not silent restore. */
  async myJobs(scope: ScopeUrl, limit = 200): Promise<AuditEntry[]> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}/my-jobs` +
        `?limit=${encodeURIComponent(String(limit))}`,
    );
    const body = await jsonOrThrow<{ jobs: AuditEntry[] }>(
      r,
      `myJobs(${scope})`,
    );
    return body.jobs;
  },

  /** Cancel an in-flight conversion the current user owns. Returns
   *  true on success, false if the row was missing / not owned /
   *  already terminal. Worker isn't notified — the bake will keep
   *  going to completion in the background but its audit row is
   *  marked cancelled and disappears from the toast. */
  async cancelMyJob(scope: ScopeUrl, jobId: string): Promise<boolean> {
    const r = await authedFetch(
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}` +
        `/my-jobs/${encodeURIComponent(jobId)}/cancel`,
      { method: "POST" },
    );
    if (r.status === 404) return false;
    await jsonOrThrow<{ job_id: string; cancelled: boolean }>(
      r,
      `cancelMyJob(${jobId})`,
    );
    return true;
  },

  /** Server-side viable-target listing. The frontend mirrors this
   * mapping client-side too, but this lets us cross-check. */
  async convertTargets(
    scope: ScopeUrl,
    sourceKey: string,
  ): Promise<TargetFormat[]> {
    const url =
      `${runtime.apiBase()}/scopes/${encodeURIComponent(scope)}` +
      `/convert/targets?source_key=${encodeURIComponent(sourceKey)}`;
    const r = await authedFetch(url);
    if (!r.ok) return [];
    const body = (await r.json()) as ConvertTargetsResponse;
    return body.targets || [];
  },
};
