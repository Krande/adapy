-- 016_audit_run_seq_and_idle.sql — friendly run number + idle-aware duration.
--
-- ``seq`` is a human-referrable monotonic run number ("Run #42"), backfilled
-- in started_at order; new runs increment via a sequence. The UUID stays the
-- canonical id; seq is just a short label an operator can refer to.
--
-- ``idle_ms`` accumulates the idle gap whenever a finished run is reopened to
-- append a later validation pass. The displayed duration is then
-- (finished_at - started_at) - idle_ms = the conversion block + the validation
-- block, instead of the wall-clock span that would wrongly include the hours
-- of idle time between a run and a validation triggered much later.

ALTER TABLE audit_runs ADD COLUMN idle_ms BIGINT NOT NULL DEFAULT 0;

-- seq: backfill existing rows in chronological order, then hand new inserts a
-- sequence default so numbering stays monotonic.
ALTER TABLE audit_runs ADD COLUMN seq BIGINT;
WITH ordered AS (
    SELECT id, ROW_NUMBER() OVER (ORDER BY started_at, id) AS rn FROM audit_runs
)
UPDATE audit_runs a SET seq = o.rn FROM ordered o WHERE a.id = o.id;
CREATE SEQUENCE IF NOT EXISTS audit_runs_seq_seq;
SELECT setval('audit_runs_seq_seq', COALESCE((SELECT MAX(seq) FROM audit_runs), 0) + 1, false);
ALTER TABLE audit_runs ALTER COLUMN seq SET DEFAULT nextval('audit_runs_seq_seq');
ALTER TABLE audit_runs ALTER COLUMN seq SET NOT NULL;
ALTER SEQUENCE audit_runs_seq_seq OWNED BY audit_runs.seq;
CREATE UNIQUE INDEX IF NOT EXISTS audit_runs_seq_idx ON audit_runs (seq);
