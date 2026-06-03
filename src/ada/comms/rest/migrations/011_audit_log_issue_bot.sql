-- 011_audit_log_issue_bot.sql — track issue-bot sync per audit_log row.
--
-- Originally (M5) the issue-bot only fired when an audit_run
-- finished — useful for release-gate sweeps but it left the more
-- pressing case unhandled: a user's regular /convert failed.
-- These rows have ``audit_run_id IS NULL`` so the bot's audit_run
-- claim never sees them. Without per-row tracking we'd either
-- spam-create issues on every poll or skip user failures entirely.
--
-- The new columns mirror the audit_runs.issue_bot_* trio so the
-- bot can claim individual rows atomically (FOR UPDATE SKIP LOCKED)
-- without restructuring its loop. Audit-run-attached rows still
-- flow through the parent run's bot pass — the partial index
-- explicitly excludes them so we never publish duplicate issues.
--
-- Values:
--   NULL       row hasn't been considered yet
--   'syncing'  bot claimed the row; sync in flight
--   'done'     issue created or commented successfully
--   'skipped'  bot intentionally skipped (no issue target configured)
--   'failed'   bot raised; ``issue_bot_last_error`` carries the
--              message and the row is eligible for manual retry

ALTER TABLE audit_log ADD COLUMN issue_bot_status TEXT;
ALTER TABLE audit_log ADD COLUMN issue_bot_synced_at TIMESTAMPTZ;
ALTER TABLE audit_log ADD COLUMN issue_bot_last_error TEXT;

-- Partial index over the bot's claim subset: failed user-driven
-- conversions only. Audit-run-attached failures are processed
-- when the parent audit_runs row flips to ``finished`` — including
-- them here would race the parent-run path and possibly produce
-- two issues for the same fingerprint.
CREATE INDEX IF NOT EXISTS audit_log_issue_bot_pending_idx
    ON audit_log (id)
    WHERE status IN ('error', 'failed')
      AND audit_run_id IS NULL
      AND issue_bot_status IS NULL;
