-- 015_audit_run_auto_validate.sql — chain a validation pass after a run.
--
-- ``auto_validate = TRUE`` on a conversion run asks the system to
-- automatically fire a follow-up ``validate_only`` (cross-format
-- parity) run for the same scope once the conversion run finishes.
-- The finished-run poller claims such runs and stamps
-- ``auto_validate_dispatched_at`` so the validation is dispatched
-- exactly once even with concurrent ticks / replicas.
--
-- ``parent_run_id`` links a child (validation, or re-dispatched) run
-- back to the run it was derived from, so the UI can show lineage and
-- the poller can tell whether a run already spawned its validation.

ALTER TABLE audit_runs ADD COLUMN auto_validate BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE audit_runs ADD COLUMN parent_run_id UUID REFERENCES audit_runs(id) ON DELETE SET NULL;
ALTER TABLE audit_runs ADD COLUMN auto_validate_dispatched_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS audit_runs_parent_run_idx
    ON audit_runs (parent_run_id) WHERE parent_run_id IS NOT NULL;
