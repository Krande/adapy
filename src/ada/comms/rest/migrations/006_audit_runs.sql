-- 006_audit_runs.sql — admin-triggered regression sweeps.
--
-- An ``audit_runs`` row represents one sweep of the converter matrix
-- across the files in a chosen scope. The admin panel creates one
-- through ``POST /api/admin/audit/runs``; the dispatcher enumerates
-- ``corpus_files × ConverterRegistry.matrix()`` and enqueues one
-- regular convert job per cell. Each of those jobs lands an ordinary
-- ``audit_log`` row with ``audit_run_id`` pointing back at the run,
-- so the per-run drill-down is a single keyed query against the
-- existing audit log instead of a parallel jobs table.
--
-- Counters (``total``, ``ok``, ``failed``, ``skipped``) get bumped by
-- ``update_audit_by_job`` when a worker writes a job's terminal
-- status — same code path as today's per-row write, plus one extra
-- UPDATE when ``audit_run_id`` is non-null.
--
-- Lifecycle:
--   running    dispatcher created the run and the jobs are firing
--   finished   total == ok + failed + skipped, finished_at set
--   aborted    admin cancelled mid-run (M4+; sets finished_at to
--              cancel time, status reflects partial progress)

CREATE TABLE IF NOT EXISTS audit_runs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Scope being audited (where the input files come from). Stored
    -- as the wire-format string ("shared" / "user:me" /
    -- "project:<id>" / "corpus:<id>" once corpora ship in M3) so the
    -- frontend can re-use scopeUrlPart helpers verbatim.
    scope         TEXT NOT NULL,
    -- ``worker_pool`` is the capability tag the dispatcher stamps
    -- on each emitted job (M2). NULL means "any worker can pick
    -- these up" — useful when the operator wants the sweep on the
    -- same pool as prod (e.g. dev deployment with one worker pod).
    worker_pool   TEXT,
    -- How the run got created. Free-text rather than a check
    -- constraint so M4's scheduler + M5's external triggers can
    -- land their own labels without a migration.
    trigger       TEXT NOT NULL DEFAULT 'manual',
    started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ,
    status        TEXT NOT NULL DEFAULT 'running',
    -- Optional human note (e.g. "release v0.8 dry run"). Surfaced in
    -- the history list so admins can label one-off sweeps.
    note          TEXT,
    -- Counters. Bumped atomically as jobs finish. ``total`` is set
    -- once at dispatch time (count of enqueued jobs); ok/failed/
    -- skipped grow until they sum to total.
    total         INTEGER NOT NULL DEFAULT 0,
    ok            INTEGER NOT NULL DEFAULT 0,
    failed        INTEGER NOT NULL DEFAULT 0,
    skipped       INTEGER NOT NULL DEFAULT 0,
    -- Audit identity of the operator who fired the run (or
    -- ``system`` for scheduled runs in M4).
    created_by    TEXT
);

-- Recent-runs view in the admin panel orders by started_at desc and
-- typically pulls the most recent N=20. Single-column DESC index
-- covers it.
CREATE INDEX IF NOT EXISTS audit_runs_started_at_idx
    ON audit_runs (started_at DESC);

-- The dispatcher's concurrent-fire guard (M4) looks for in-progress
-- runs with the same (scope, worker_pool). A partial index keeps the
-- check cheap as finished_at fills in.
CREATE INDEX IF NOT EXISTS audit_runs_running_idx
    ON audit_runs (scope, worker_pool)
    WHERE status = 'running';

-- Link audit_log rows back to their parent run. NULL on every row
-- predating this migration and on every regular user-triggered
-- convert / upload / download row going forward — only the
-- dispatcher writes a non-null value.
ALTER TABLE audit_log ADD COLUMN audit_run_id UUID
    REFERENCES audit_runs (id) ON DELETE SET NULL;

-- Drill-in query is "all rows for this run, ordered by ts asc" — the
-- partial index narrows to the rows that actually matter.
CREATE INDEX IF NOT EXISTS audit_log_audit_run_id_idx
    ON audit_log (audit_run_id, ts)
    WHERE audit_run_id IS NOT NULL;
