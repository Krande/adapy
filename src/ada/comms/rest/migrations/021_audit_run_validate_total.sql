-- 021_audit_run_validate_total.sql — count the validation pass upfront.
--
-- ``validate_total`` reserves the auto-validation parity cells inside a
-- run's ``total`` at *initial* dispatch, so the run's cell count is
-- complete from the start instead of growing when the validation pass
-- begins. While the reservation is outstanding the run stays 'running'
-- (the counter-bump finish check compares against the full total); the
-- auto-validate poller claims the run once the conversion cells alone
-- have landed (ok + failed + skipped >= total - validate_total) and the
-- validation dispatch *consumes* the reservation — re-counting the
-- parity cells against the scope's current files and folding any drift
-- into ``total`` — rather than extending it.
--
-- Runs without auto-validate (and pre-migration rows) keep
-- validate_total = 0 and the old extend-on-finish behaviour.

ALTER TABLE audit_runs ADD COLUMN validate_total INT NOT NULL DEFAULT 0;
