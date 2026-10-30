-- 047_asset_sweep_run_warnings.sql -- what a run wants its reader to know, beside its items.
--
-- `warnings` is a list of sentences the driver adds while it runs -- an apply-changes run names
-- every collection it left out because the provider store holds no full export of it yet
-- (routes/asset_apply_changes.py). `summary` is a small object of run-level facts: an apply-changes
-- run's `queued_full_exports: {run_id, collections}`, and on the follow-up run it queued,
-- `apply_changes_run_id` -- the two runs link to each other.

ALTER TABLE asset_sweep_runs
    ADD COLUMN IF NOT EXISTS warnings JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS summary  JSONB NOT NULL DEFAULT '{}'::jsonb;
