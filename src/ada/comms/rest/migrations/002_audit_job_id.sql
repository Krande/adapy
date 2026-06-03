-- 002_audit_job_id.sql — link audit_log rows to convert jobs so the
-- worker can flip a row from 'queued' to 'done'/'error' once the
-- conversion finishes.
--
-- Conversion audit rows are inserted at enqueue time (status='queued')
-- by the API. Without this column the row stays 'queued' forever, even
-- on failure — confusing in the admin panel. The worker uses job_id
-- as the lookup key.
--
-- Existing rows pre-dating this migration have job_id = NULL; their
-- status reflects whatever the API wrote (typically 'queued'). They
-- can't be retroactively fixed and that's acceptable historical data.

ALTER TABLE audit_log ADD COLUMN job_id TEXT;

-- Partial index — only the small subset of rows tied to a queue job
-- needs the lookup; download/upload rows have NULL and would just
-- bloat a full-table index.
CREATE INDEX audit_log_job_id_idx ON audit_log(job_id) WHERE job_id IS NOT NULL;
