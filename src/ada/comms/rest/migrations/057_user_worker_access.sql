-- 057_user_worker_access.sql -- per-user and per-capability server worker pool access.
--
-- Interactive compute workloads default to browser WASM/Pyodide; server worker pods
-- are reserved for admins, scheduled background tasks, and users explicitly granted
-- access here (globally via `enabled = true` or selectively via `allowed_capabilities`).

CREATE TABLE IF NOT EXISTS user_worker_access (
    user_sub             TEXT PRIMARY KEY REFERENCES users(sub) ON DELETE CASCADE,
    enabled              BOOLEAN NOT NULL DEFAULT FALSE,
    allowed_capabilities TEXT[] NOT NULL DEFAULT '{}',
    updated_by           TEXT,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
