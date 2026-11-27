-- 058_execution_policy.sql -- which engine runs each operation (native wasm, Pyodide, worker pool).
--
-- One global policy document (a single row) and per-user overrides. A document is a
-- JSON object keyed by operation, then by qualifier, each value an ordered fallback
-- list of engines; validated by ada.comms.rest.execution_policy before it is stored.
-- No row = the built-in defaults (execution_policy.DEFAULT_POLICY).

CREATE TABLE IF NOT EXISTS execution_policy (
    id         SMALLINT PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    policy     JSONB NOT NULL,
    updated_by TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS user_execution_policy (
    user_sub   TEXT PRIMARY KEY REFERENCES users(sub) ON DELETE CASCADE,
    policy     JSONB NOT NULL,
    updated_by TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
