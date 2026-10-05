-- 032_audit_issue_recheck.sql — re-run an issue's failing cells and close it when they pass.
--
-- The issue-bot files one issue per failure FINGERPRINT (label ``audit-fp:<hash>``), but
-- nothing recorded which audit_log rows produced a fingerprint, so an issue could not be
-- traced back to the cells that would prove it fixed. ``issue_fp`` is that link. It is a
-- pure function of the row (source suffix, target, error, traceback), so the issue-bot
-- poller backfills it for every failed row, old ones included, in small batches.
--
-- A recheck is an ordinary audit run (``trigger='issue-recheck'``) over exactly the cells
-- that reproduced one or more fingerprints. ``audit_issue_rechecks`` remembers which
-- fingerprints a run is checking and which cells each one asked for, so when the run
-- finishes the issue-bot can give each fingerprint a verdict:
--
--   NULL            the run has not finished yet
--   'fixed'         every cell passed -- the issue was commented on and closed
--   'reproduced'    a cell failed with the same fingerprint -- the issue stays open
--   'changed'       a cell failed differently -- the issue stays open, the new
--                   fingerprint is synced the normal way
--   'unverifiable'  a cell could not be re-run (source gone, cell skipped)
--   'error'         the verdict could not be published to the forge

ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS issue_fp TEXT;

CREATE INDEX IF NOT EXISTS audit_log_issue_fp_idx
    ON audit_log (issue_fp)
    WHERE issue_fp IS NOT NULL;

-- The backfill's claim subset: failed rows not fingerprinted yet.
CREATE INDEX IF NOT EXISTS audit_log_issue_fp_pending_idx
    ON audit_log (id)
    WHERE status IN ('error', 'failed') AND issue_fp IS NULL;

CREATE TABLE IF NOT EXISTS audit_issue_rechecks (
    id             BIGSERIAL PRIMARY KEY,
    fp             TEXT NOT NULL,
    run_id         UUID NOT NULL REFERENCES audit_runs (id) ON DELETE CASCADE,
    -- [[source_key, target_format], ...] this run re-runs for ``fp``
    cells          JSONB NOT NULL,
    created_by     TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    verdict        TEXT,
    verdict_detail TEXT,
    verdict_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS audit_issue_rechecks_fp_idx
    ON audit_issue_rechecks (fp, created_at DESC);
CREATE INDEX IF NOT EXISTS audit_issue_rechecks_run_idx
    ON audit_issue_rechecks (run_id);
