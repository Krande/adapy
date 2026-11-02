-- 052_audit_log_extra_seconds.sql — a running job's time limit, extended while it runs.
--
-- A plugin job gives up on a step after its time limit, and a job seen at 90 % near that limit
-- should not have to start over. ``extra_seconds`` is what an admin (or the job's owner) has
-- added to it since the job started: the worker's cancel poller reads it into the plugin's
-- ``job_limits``, and a run waiting on the job (a sweep) extends its own wait by as much.

ALTER TABLE audit_log ADD COLUMN extra_seconds INTEGER NOT NULL DEFAULT 0;
