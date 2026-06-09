-- 014_audit_parity.sql — cross-format visual-parity results.
--
-- One row per (audit_run, source) parity check. The pass/fail is also reflected
-- on the source's parity audit_log cell (action='validate', status='error' on a
-- mismatch) so the existing run grid / --failed view surface it; this table
-- carries the structured per-format detail the audit_log columns can't hold.
CREATE TABLE IF NOT EXISTS audit_parity (
    id            BIGSERIAL PRIMARY KEY,
    ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    audit_run_id  UUID REFERENCES audit_runs(id) ON DELETE CASCADE,
    job_id        TEXT,
    source_key    TEXT NOT NULL,
    baseline      INTEGER NOT NULL,                     -- expected (source) element count
    counts        JSONB NOT NULL,                       -- {"source":4,"ifc":4,"xml":4,"step":4}
    consistent    BOOLEAN NOT NULL,
    mismatches    JSONB NOT NULL DEFAULT '{}'::jsonb,   -- {"ifc":3}
    errors        JSONB NOT NULL DEFAULT '{}'::jsonb    -- {"step":"RuntimeError: ..."}
);

CREATE INDEX IF NOT EXISTS audit_parity_run_idx ON audit_parity (audit_run_id);
CREATE INDEX IF NOT EXISTS audit_parity_source_idx ON audit_parity (source_key);
