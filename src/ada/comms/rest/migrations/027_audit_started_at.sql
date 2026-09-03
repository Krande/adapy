-- 027_audit_started_at.sql — record when a worker actually began a job, so
-- queue wait can be measured after the fact.
--
-- ``ts`` is the ENQUEUE time (insert_audit writes the row as 'queued' before a
-- worker can see the job, and the completion update never rewrites it), and
-- ``duration_ms`` is processing time. The instant between them — how long the
-- job sat in the queue — was simply not stored, so the audit Overview could
-- report the wait of jobs still WAITING and nothing about jobs that had run.
-- Congestion was therefore only visible while it was happening.
--
-- With this, wait = started_at - ts for any row a worker picked up, which makes
-- "was it slow because it queued, or because it ran slowly" answerable from
-- history rather than from watching.
--
-- Nullable, and deliberately not backfilled: rows written before this migration
-- have no honest value to give, and inventing one (ts, say) would report every
-- historical job as having waited zero and quietly flatten exactly the trend
-- this column exists to show. NULL means "not recorded", and the queries
-- exclude it.
ALTER TABLE audit_log ADD COLUMN started_at TIMESTAMPTZ;

-- Partial: only rows that actually started are ever scanned for wait stats,
-- and on a long-lived table most rows predate the column.
CREATE INDEX IF NOT EXISTS audit_log_started_at_idx
    ON audit_log (started_at DESC)
    WHERE started_at IS NOT NULL;
