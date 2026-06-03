-- 009_audit_issue_bot.sql — track per-run issue-bot sync status.
--
-- The issue-bot (M5) polls finished audit_runs that haven't been
-- synced yet, computes failure fingerprints, and creates / comments
-- on issues in the configured forgejo or github repo. Per-run status
-- columns let the bot pick up the next unsynced run atomically + let
-- the admin UI show a "synced / failed / not yet" badge per row.
--
-- Values for ``issue_bot_status``:
--   NULL        run hasn't been considered yet by the bot
--   'syncing'   bot has claimed the row and is mid-call
--   'done'      bot completed the sync (issues created/commented)
--   'skipped'   bot intentionally skipped (no failures, or no
--               issue target configured) — terminal but distinct
--               from a real successful sync so the UI can label it
--   'failed'    bot crashed; ``issue_bot_last_error`` carries the
--               message and the row is eligible for manual retry
--
-- ``issue_bot_synced_at`` records the terminal-state timestamp so
-- the admin panel can show "synced 3m ago" without a separate join.

ALTER TABLE audit_runs ADD COLUMN issue_bot_status TEXT;
ALTER TABLE audit_runs ADD COLUMN issue_bot_last_error TEXT;
ALTER TABLE audit_runs ADD COLUMN issue_bot_synced_at TIMESTAMPTZ;

-- The poller picks the oldest finished run that hasn't been touched
-- yet. Partial index on the eligible subset keeps the query cheap
-- as the table grows.
CREATE INDEX IF NOT EXISTS audit_runs_issue_bot_pending_idx
    ON audit_runs (finished_at)
    WHERE status = 'finished' AND issue_bot_status IS NULL;
