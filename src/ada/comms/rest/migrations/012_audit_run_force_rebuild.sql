-- 012_audit_run_force_rebuild.sql — opt-in cache bypass for
-- audit sweeps.
--
-- The audit dispatcher's default fast-path skips any cell whose
-- derived blob (the convert output, ``_derived/<src>.<fmt>``) is
-- already in storage from a previous run — it marks the cell as
-- ``done`` immediately and never enqueues a job. That's the right
-- default for everyday "is anything regressing?" sweeps, but
-- makes the audit useless as a perf benchmark: two sequential
-- runs against the same scope short-circuit ~80% of cells and the
-- total wall time drops to whatever the failure-cells + the
-- previously-failing-now-passing-cells consume.
--
-- ``force_rebuild = TRUE`` tells the dispatcher to skip the
-- ``storage.exists()`` check and enqueue every cell as if its
-- output didn't exist. Used when measuring the impact of perf
-- work — e.g. after fixing a hot path, run with force_rebuild to
-- get a clean wall-time + RSS number.

ALTER TABLE audit_runs ADD COLUMN force_rebuild BOOLEAN NOT NULL DEFAULT FALSE;
