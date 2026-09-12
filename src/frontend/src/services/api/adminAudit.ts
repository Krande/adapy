// Admin audit: the conversion audit log/summary, audit runs (create,
// re-dispatch, rerun a cell, validate, cancel, delete, list, cell
// history), audit schedules, and the issue-bot sync target.

import { runtime } from "@/runtime/config";

import {
  ApiError,
  authedFetch,
  jsonOrThrow,
  readDetail,
  toQueryString,
  type ScopeUrl,
} from "./client";
import type { AuditEntry, AuditFilters, AuditSummary } from "./types/audit";

// One audit-sweep record. Returned by /admin/audit/runs endpoints.
// Counters are eventually-consistent — total is set once the
// dispatcher finishes enumerating cells; ok/failed/skipped advance
// as worker outcomes land. status flips to 'finished' when their
// sum equals total.
export interface AuditRun {
  id: string;
  // Short, human-referrable monotonic run number ("Run #42"); the UUID id
  // stays canonical. May be absent on rows from before the seq migration.
  seq?: number | null;
  // Idle time (ms) excluded from the active duration — the gap before a
  // later validation pass folded into the run. UI subtracts it.
  idle_ms?: number | null;
  // Sum of every cell's own duration_ms — the run's active compute time,
  // independent of wall clock. The UI shows this as the run's total runtime.
  cells_duration_ms?: number | null;
  scope: string;
  worker_pool: string | null;
  trigger: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  note: string | null;
  total: number;
  // Auto-validation parity cells counted into `total` upfront but not yet
  // enqueued (the poller dispatches them once the conversion cells land).
  // 0 once the validation pass starts, and for non-auto-validate runs.
  validate_total?: number;
  ok: number;
  failed: number;
  skipped: number;
  created_by: string | null;
  // M7+: when true, the dispatcher bypassed the cached-blob
  // short-circuit. Useful as a UI badge so an unexpected slow
  // run is recognisable as a perf measurement vs a regression.
  force_rebuild: boolean;
  // When true, the finished-run poller auto-fires a follow-up
  // validate_only parity run for the same scope once this run finishes.
  auto_validate?: boolean;
  // Set once a validation pass has been dispatched for this run (via the
  // toggle or the manual button) — used to gate the "Validate" button so a
  // run is validated at most once.
  auto_validate_dispatched_at?: string | null;
  // Set on a derived run (the auto-validation child, or a re-dispatched
  // copy) to the run it was created from.
  parent_run_id?: string | null;
  // M5: issue-bot sync status. NULL until the bot has touched the
  // run; 'syncing' while in flight; terminal 'done'/'skipped'/'failed'.
  issue_bot_status: string | null;
  issue_bot_last_error: string | null;
  issue_bot_synced_at: string | null;
}

// One historic result for a (source key, target_format) cell — newest
// first — backing the grid's right-click "show history" table.
export interface AuditCellHistoryRow {
  id: number;
  ts: string | null;
  status: string;
  error: string | null;
  duration_ms: number | null;
  peak_rss_kb: number | null;
  worker_image_tag: string | null;
  audit_run_id: string | null;
}

// Per-deployment configuration for the audit-failure → issue tracker
// bridge. ``token_env_name`` references the env var that carries the
// token (sourced from a k8s Secret); ``token_present`` reflects
// whether that env var is set on the serving API replica.
export interface IssueTargetConfig {
  kind: "disabled" | "github" | "forgejo";
  repo: string;
  base_url: string;
  token_env_name: string;
  token_present: boolean;
}

// One audit_log row scoped to a parent audit_run. Narrower projection
// than ``AuditEntry`` — the grid view doesn't need user_sub /
// scope_kind / traceback (all redundant for cells in one run).
export interface AuditRunJob {
  id: number;
  ts: string | null;
  key: string | null;
  target_format: string | null;
  status: string | null;
  error: string | null;
  duration_ms: number | null;
  cpu_user_ms: number | null;
  cpu_sys_ms: number | null;
  peak_rss_kb: number | null;
  read_bytes: number | null;
  write_bytes: number | null;
  job_id: string | null;
  // Image tag of the worker pod that processed this cell.
  // Empty for cells finished before migration 013 / cells that
  // hit the dispatcher's cached short-circuit.
  worker_image_tag: string | null;
  // Per-conversion provenance + quality flags (JSONB). Includes
  // ``occ_fallback`` ({count, reasons, geoms}) when the NGEOM/libtess2 path
  // silently fell back to OCC, and ``mesh_flags`` for distorted triangles.
  convert_meta?: {
    occ_fallback?: {
      count: number;
      reasons?: Record<string, number>;
      geoms?: Record<string, number>;
    };
    mesh_flags?: {
      distorted_tris?: number;
      distorted_frac?: number;
      n_tris?: number;
    };
    // Faces with a trim boundary that tessellated to zero triangles (silently dropped geometry).
    geom_health?: { dropped_faces?: number; total_faces?: number };
    [k: string]: unknown;
  } | null;
}

// One recurring audit schedule. The API scheduler tick fires the
// row's (scope, worker_pool) sweep every ``cron_expr`` slot. The UI
// shows ``next_fire_at`` so admins know when the next run lands;
// ``last_skipped_reason`` surfaces when a tick decided not to
// dispatch (e.g. concurrent-fire guard).
export interface AuditSchedule {
  id: string;
  name: string;
  cron_expr: string;
  scope: string;
  worker_pool: string | null;
  enabled: boolean;
  last_fired_at: string | null;
  next_fire_at: string | null;
  last_skipped_reason: string | null;
  created_at: string | null;
  created_by: string | null;
  archived_at: string | null;
}

export const adminAuditApi = {
  /** Admin: paged audit log. ``before_id`` is the keyset cursor —
   * pass ``next_before_id`` from the previous page to get the next
   * older one. Returns null for ``next_before_id`` when at the end. */
  async adminAudit(
    filters: AuditFilters = {},
  ): Promise<{ entries: AuditEntry[]; next_before_id: number | null }> {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(filters)) {
      if (v !== undefined && v !== "" && v !== null) params.set(k, String(v));
    }
    const qs = toQueryString(params);
    const url = `${runtime.apiBase()}/admin/audit${qs ? `?${qs}` : ""}`;
    const r = await authedFetch(url);
    return jsonOrThrow(r, "adminAudit");
  },

  /** Admin: aggregate counts for the audit Overview, under the same filter
   * the log uses. ``status`` is intentionally not sent — the summary shows how
   * a population splits across states, and the tiles are what set that filter,
   * so honouring it would zero every tile but the selected one. */
  async adminAuditSummary(filters: AuditFilters = {}): Promise<AuditSummary> {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(filters)) {
      if (k === "status" || k === "limit" || k === "before_id") continue;
      if (v !== undefined && v !== "" && v !== null) params.set(k, String(v));
    }
    const qs = toQueryString(params);
    const url = `${runtime.apiBase()}/admin/audit/summary${qs ? `?${qs}` : ""}`;
    const r = await authedFetch(url);
    return jsonOrThrow(r, "adminAuditSummary");
  },

  /** Admin: kick off a regression sweep across one scope. Enumerates
   * every (source file × viable target) cell from the converter
   * matrix and enqueues a normal convert job per cell with the
   * resulting audit_run id stamped on each row. Cached cells (derived
   * blob already present) count as ``done`` immediately. Returns the
   * fresh run record (status='running', total=0); poll
   * ``adminAuditRunGet`` for progress as the dispatcher fills in
   * ``total`` and counters update as jobs land. */
  async adminAuditRunCreate(body: {
    scope: ScopeUrl;
    worker_pool?: string | null;
    note?: string | null;
    force_rebuild?: boolean;
    validate_only?: boolean;
    auto_validate?: boolean;
  }): Promise<AuditRun> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/audit/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return jsonOrThrow(r, "adminAuditRunCreate");
  },

  /** Admin: re-run a prior audit against the same scope / pool / settings.
   * The cells are re-enumerated from the scope at dispatch time, so the
   * re-run reflects the scope's current files. Returns the new run. */
  async adminAuditRunReDispatch(runId: string): Promise<AuditRun> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/runs/${encodeURIComponent(runId)}/re-dispatch`,
      { method: "POST" },
    );
    return jsonOrThrow(r, `adminAuditRunReDispatch(${runId})`);
  },

  /** Admin: re-run a single cell of a run in place (right-click → Rerun).
   * Enqueues one force-rebuild conversion for (key, target) against the run's
   * own scope/pool and reopens the run; the other cells are untouched.
   * Returns the (reopened) run. */
  async adminAuditRunRerunCell(
    runId: string,
    key: string,
    target: string,
  ): Promise<AuditRun> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/runs/${encodeURIComponent(runId)}/rerun-cell`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key, target }),
      },
    );
    return jsonOrThrow(r, `adminAuditRunRerunCell(${runId})`);
  },

  /** Admin: append a validation (cross-format parity) pass to a finished
   * run — folded into the same run, not a new one. 409 if the run isn't
   * finished or has already been validated. Returns the (reopened) run. */
  async adminAuditRunValidate(runId: string): Promise<AuditRun> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/runs/${encodeURIComponent(runId)}/validate`,
      { method: "POST" },
    );
    return jsonOrThrow(r, `adminAuditRunValidate(${runId})`);
  },

  /** Admin: delete an audit run and its audit_log rows (parity cascades).
   * 409 if the run is still running — cancel it first. */
  async adminAuditRunDelete(runId: string): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/runs/${encodeURIComponent(runId)}`,
      { method: "DELETE" },
    );
    if (!r.ok) {
      throw new ApiError(
        `adminAuditRunDelete(${runId})`,
        r.status,
        await readDetail(r),
      );
    }
  },

  /** Admin: historic results for one (source key, target_format) cell across
   * every run, newest first. Backs the grid's right-click "show history". */
  async adminAuditCellHistory(
    key: string,
    target: string,
    limit = 50,
  ): Promise<{
    key: string;
    target_format: string;
    history: AuditCellHistoryRow[];
  }> {
    const params = new URLSearchParams({ key, target, limit: String(limit) });
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/cell-history?${params.toString()}`,
    );
    return jsonOrThrow(r, "adminAuditCellHistory");
  },

  /** Cell matrix for an audit run — drives the in-browser (WASM) sweep
   * executor. ``done`` flags cells that already have a terminal audit row
   * for this run, so a reload resumes instead of re-running them. */
  async adminAuditRunCells(runId: string): Promise<{
    run_id: string;
    scope: ScopeUrl;
    cells: Array<{ source_key: string; target_format: string; done: boolean }>;
  }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/runs/${encodeURIComponent(runId)}/cells`,
    );
    return jsonOrThrow(r, "adminAuditRunCells");
  },

  /** Admin: ambient summary of currently-running audit sweeps.
   * Drives the bottom-right badge that links into the audit Runs
   * tab; intentionally cheap so it polls cleanly every 15s.
   * ``current_cell`` surfaces what's actively converting right now
   * (most-recently-touched ``running`` or ``queued`` audit_log row
   * across all live runs). */
  async adminAuditActive(): Promise<{
    running_runs: number;
    pending_cells: number;
    current_cell: {
      key: string | null;
      target_format: string | null;
      status: string | null;
      started_at: string | null;
      elapsed_ms: number | null;
    } | null;
  }> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/audit/active`);
    return jsonOrThrow(r, "adminAuditActive");
  },

  /** Admin: recent audit runs, reverse-chronological. ``before_started_at``
   * is the keyset cursor (ISO timestamp) — pass the previous response's
   * ``next_before_started_at`` to page back further. */
  async adminAuditRunsList(opts?: {
    limit?: number;
    before_started_at?: string | null;
  }): Promise<{ runs: AuditRun[]; next_before_started_at: string | null }> {
    const params = new URLSearchParams();
    if (opts?.limit) params.set("limit", String(opts.limit));
    if (opts?.before_started_at)
      params.set("before_started_at", opts.before_started_at);
    const qs = toQueryString(params);
    const url = `${runtime.apiBase()}/admin/audit/runs${qs ? `?${qs}` : ""}`;
    const r = await authedFetch(url);
    return jsonOrThrow(r, "adminAuditRunsList");
  },

  /** Admin: one run + every audit_log row tied to it. The job list
   * powers the per-cell grid view (files × targets) in the audit
   * panel. Returned in dispatch order (asc by audit_log.id) so
   * grid rendering is deterministic. */
  async adminAuditRunGet(
    runId: string,
  ): Promise<{ run: AuditRun; jobs: AuditRunJob[] }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/runs/${encodeURIComponent(runId)}`,
    );
    return jsonOrThrow(r, `adminAuditRunGet(${runId})`);
  },

  /** Admin: abort a running audit. Flips the run to ``aborted``
   * and cancels every queued / running child cell in one
   * transaction. 404 if the run isn't currently ``running``. */
  async adminAuditRunCancel(runId: string): Promise<AuditRun> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/runs/${encodeURIComponent(runId)}/cancel`,
      { method: "POST" },
    );
    return jsonOrThrow(r, `adminAuditRunCancel(${runId})`);
  },

  /** Admin: list live audit schedules (M4). Archived rows hidden;
   * the picker only ever wants currently-firing rows. */
  async adminAuditSchedulesList(): Promise<{ schedules: AuditSchedule[] }> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/audit/schedules`);
    return jsonOrThrow(r, "adminAuditSchedulesList");
  },

  /** Admin: create a recurring schedule. ``cron_expr`` is validated
   * server-side via croniter — invalid expressions return 400 with
   * the croniter parse error in the body. */
  async adminAuditScheduleCreate(body: {
    name: string;
    cron_expr: string;
    scope: string;
    worker_pool?: string | null;
    enabled?: boolean;
  }): Promise<AuditSchedule> {
    const r = await authedFetch(`${runtime.apiBase()}/admin/audit/schedules`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    return jsonOrThrow(r, "adminAuditScheduleCreate");
  },

  /** Admin: partial update. Only included keys are written; omit
   * a field to leave it alone. Editing ``cron_expr`` recomputes
   * ``next_fire_at`` so the retimed pattern takes effect right
   * away. */
  async adminAuditScheduleUpdate(
    scheduleId: string,
    body: Partial<{
      name: string;
      cron_expr: string;
      scope: string;
      worker_pool: string | null;
      enabled: boolean;
    }>,
  ): Promise<AuditSchedule> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/schedules/${encodeURIComponent(scheduleId)}`,
      {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    return jsonOrThrow(r, `adminAuditScheduleUpdate(${scheduleId})`);
  },

  /** Admin: soft-delete a schedule. The tick filter excludes
   * archived rows so the schedule stops firing immediately. */
  async adminAuditScheduleArchive(scheduleId: string): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/schedules/${encodeURIComponent(scheduleId)}`,
      { method: "DELETE" },
    );
    if (!r.ok) {
      throw new ApiError(
        `adminAuditScheduleArchive(${scheduleId})`,
        r.status,
        await readDetail(r),
      );
    }
  },

  /** Admin: fire a schedule's sweep right now, bypassing the cron
   * slot. Honours the concurrent-fire guard (409 if a previous
   * run with the same (scope, pool) is still in-flight). Does NOT
   * advance ``next_fire_at``. */
  async adminAuditScheduleFireNow(scheduleId: string): Promise<AuditRun> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/schedules/${encodeURIComponent(scheduleId)}/fire`,
      { method: "POST" },
    );
    return jsonOrThrow(r, `adminAuditScheduleFireNow(${scheduleId})`);
  },

  /** Admin: read the configured issue-tracker target (M5). Tokens
   * never come back — only the env var name + a present/missing
   * flag for the serving replica. */
  async adminIssueTargetGet(): Promise<IssueTargetConfig> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/issue-target`,
    );
    return jsonOrThrow(r, "adminIssueTargetGet");
  },

  /** Admin: overwrite the issue-tracker target. The actual token
   * is rotated by changing the underlying k8s Secret + re-rolling
   * the API deployment; this endpoint only points at which env
   * var to read. */
  async adminIssueTargetSet(body: {
    kind: "disabled" | "github" | "forgejo";
    repo: string;
    base_url?: string;
    token_env_name?: string;
  }): Promise<IssueTargetConfig> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/issue-target`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    return jsonOrThrow(r, "adminIssueTargetSet");
  },

  /** Admin: re-run the issue-bot sync for one finished audit run.
   * Clears the prior ``issue_bot_status`` and kicks an immediate
   * sync as a background task so the user gets quick feedback. */
  async adminAuditRunSyncIssues(runId: string): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/runs/${encodeURIComponent(runId)}/sync-issues`,
      { method: "POST" },
    );
    if (!r.ok) {
      throw new ApiError(
        `adminAuditRunSyncIssues(${runId})`,
        r.status,
        await readDetail(r),
      );
    }
  },

  /** Admin: re-run the issue-bot for ONE failed user conversion
   * (M5b). Mirrors adminAuditRunSyncIssues; the response is 202
   * + the row gets re-claimed by the bot's background task. */
  async adminAuditLogSyncIssue(auditId: number): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/${auditId}/sync-issue`,
      { method: "POST" },
    );
    if (!r.ok) {
      throw new ApiError(
        `adminAuditLogSyncIssue(${auditId})`,
        r.status,
        await readDetail(r),
      );
    }
  },

  /** Admin: the client_metrics payload for one browser view/render
   * audit row — per-phase split + device + per-function frames. Backs
   * the audit-log detail "Client" tab. */
  async adminAuditClientMetrics(id: number): Promise<{
    audit_id: number;
    client_metrics: Record<string, unknown> | null;
  }> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/${id}/client-metrics`,
    );
    return jsonOrThrow(r, "adminAuditClientMetrics");
  },

  /** Admin: fetch a conversion's captured stdout/stderr log (the log_key blob) as text. Throws
   * ApiError(404) when the row has no log attached (predates log capture, or no conversion ran). */
  async adminGetAuditLog(auditId: number): Promise<string> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/${auditId}/log`,
    );
    if (!r.ok) {
      throw new ApiError(
        `adminGetAuditLog(${auditId})`,
        r.status,
        await readDetail(r),
      );
    }
    return r.text();
  },

  /** Trigger the original-source download for an audit row. Used by
   * the local repro pixi tasks but also handy for one-off debugging
   * straight from the admin panel. */
  async adminDownloadAuditSource(
    auditId: number,
    suggestedName: string,
  ): Promise<void> {
    const r = await authedFetch(
      `${runtime.apiBase()}/admin/audit/${auditId}/source`,
    );
    if (!r.ok) {
      throw new ApiError(
        `adminDownloadAuditSource(${auditId})`,
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
};
