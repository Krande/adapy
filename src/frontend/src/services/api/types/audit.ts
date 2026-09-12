// Audit-log DTOs shared by the user-facing job list (services/api/conversion.ts's
// myJobs) and the admin audit log/runs (services/api/adminAudit.ts) — both
// endpoints return the same row shape, just filtered differently server-side.

export interface AuditEntry {
  id: number;
  ts: string | null;
  user_sub: string | null;
  user_email: string | null;
  user_display_name: string | null;
  scope_kind: string;
  scope_id: string | null;
  action: string;
  key: string | null;
  target_format: string | null;
  status: string | null;
  error: string | null;
  duration_ms: number | null;
  traceback: string | null;
  cpu_user_ms: number | null;
  cpu_sys_ms: number | null;
  peak_rss_kb: number | null;
  read_bytes: number | null;
  write_bytes: number | null;
  profile_key: string | null;
  job_id: string | null;
  // M5b: per-row issue-bot sync state for failed user conversions.
  // NULL until the bot has touched the row. Audit-run-attached
  // failures (audit_run_id IS NOT NULL) are processed via the
  // parent run's pass and leave this column NULL by design.
  audit_run_id: string | null;
  issue_bot_status: string | null;
  issue_bot_synced_at: string | null;
  issue_bot_last_error: string | null;
  // Stable per-device id (from client_metrics) — distinguishes view/render
  // audit logs by device (e.g. phone vs desktop). Null for server-side rows.
  device_id: string | null;
  // The worker image that processed a convert row; links to its package manifest.
  worker_image_tag: string | null;
  // Conversion engine + effective toggles for a convert row (which tessellator
  // actually ran, incl. an adacpp→occ-builtin fallback, + the options used).
  convert_meta: ConvertMeta | null;
}

export interface ConvertMeta {
  tessellator?: string;
  step_glb_pipeline?: string;
  glb_compression?: string;
  stream_workers?: string | number | null;
  // Wall-clock split of the recorded duration: the conversion proper vs the
  // GLB-compression (meshopt) post-step, so compression cost isn't mistaken
  // for a slower conversion.
  convert_ms?: number | null;
  compress_ms?: number | null;
  // The pod's CPU allotment (cgroup quota) at conversion time, so the metrics chart can render CPU
  // as % utilization across all cores rather than a cumulative ramp.
  cpu_cores?: number | null;
  options?: Record<string, string>;
  // adacpp [STEPPROF-JSON] pipeline summaries, parsed from the captured child log when
  // the profile_conversions toggle was on — one entry per instrumented C++ pipeline run.
  cpp_profile?: CppProfile[];
}

export interface CppProfilePhase {
  name: string;
  ms: number;
  rss_mb: number;
}

export interface CppProfileThread {
  tid: number;
  solids: number;
  busy_ms: number;
}

export interface CppProfile {
  label: string;
  wall_ms: number;
  peak_rss_mb: number;
  cpu_s?: number;
  parallelism?: number;
  vctx?: number;
  nvctx?: number;
  disk_read_mb?: number;
  majflt?: number;
  solids?: number;
  tris?: number;
  max_tris_solid?: number;
  phases: CppProfilePhase[];
  notes?: Record<string, number>;
  threads?: CppProfileThread[];
}

export interface AuditFilters {
  user_sub?: string;
  scope_kind?: string;
  scope_id?: string;
  action?: string;
  /** Conversion target format (glb / ifc / step / …). */
  target?: string;
  /** Job state (queued / running / done / error). */
  status?: string;
  /** Case-insensitive substring filter on the source filepath/filename. */
  key?: string;
  /** Lower bound on ``ts``: a duration the SERVER resolves ("6h", "30d"), or an
   * ISO-8601 instant for a custom range. Relative forms are deliberately not
   * resolved here — a clock a few minutes fast would silently empty a
   * "last 5 minutes" view. */
  since?: string;
  /** Upper bound on ``ts``, ISO-8601. Only set for a custom range. */
  until?: string;
  before_id?: number;
  limit?: number;
}

/** Aggregate counts behind the Audit tab's Overview. */

/** Queue pressure right now. Ages are of jobs still WAITING — `ts` is the
 * enqueue time, so a queued row's age is its wait so far. Jobs that already ran
 * are absent on purpose: nothing records when a worker picked one up, so a
 * historical wait would have to be invented. */
export interface AuditCongestion {
  queued: number;
  running: number;
  /** Null when nothing is queued — distinct from 0, which would mean
   * "served instantly". */
  oldest_wait_s: number | null;
  mean_wait_s: number | null;
  median_wait_s: number | null;
  /** How many rows carry a recorded start. Rows predating the started_at
   * migration do not, so this travels with the numbers — a median over three
   * rows deserves less trust than one over three thousand. */
  served: number;
  served_mean_wait_s: number | null;
  served_median_wait_s: number | null;
  served_p95_wait_s: number | null;
  served_max_wait_s: number | null;
}

export interface AuditSummary {
  total: number;
  congestion: AuditCongestion;
  /** Always carries the four states the queue writes, zero-filled. */
  by_status: Record<string, number>;
  by_target: { target: string; counts: Record<string, number>; total: number }[];
  top_errors: { error: string; count: number }[];
}
