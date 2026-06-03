-- 001_initial.sql — schema for the multi-tenant REST viewer.
--
-- Lazy user upsert: rows are created on first authenticated request,
-- not pre-provisioned. `sub` is the durable id from the OIDC token's
-- `sub` claim — never email, which can change.
--
-- Project membership is the application's job (managed via the admin
-- panel later in phase 3). OIDC groups drive *roles* (admin vs not),
-- not project membership; that distinction stays out of identity
-- infrastructure where it doesn't belong.
--
-- audit_log is a superset of "conversion log": any storage- or
-- conversion-relevant action lands here, with enough fields for the
-- admin panel to filter by user / project / status / time.

CREATE TABLE users (
    sub           TEXT PRIMARY KEY,
    email         TEXT,
    display_name  TEXT,
    last_seen_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE projects (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug         TEXT UNIQUE NOT NULL,
    name         TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    archived_at  TIMESTAMPTZ
);

CREATE TABLE project_members (
    project_id   UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    user_sub     TEXT NOT NULL REFERENCES users(sub) ON DELETE CASCADE,
    role         TEXT NOT NULL DEFAULT 'member',
    added_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (project_id, user_sub)
);

CREATE INDEX project_members_user_idx ON project_members(user_sub);

CREATE TABLE audit_log (
    id            BIGSERIAL PRIMARY KEY,
    ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    user_sub      TEXT,
    scope_kind    TEXT NOT NULL,           -- 'shared' | 'project' | 'user'
    scope_id      TEXT,                    -- project_id (string) or user_sub or NULL for shared
    action        TEXT NOT NULL,           -- 'upload' | 'download' | 'view' | 'convert'
    key           TEXT,
    target_format TEXT,
    status        TEXT,                    -- 'ok' | 'error' | 'queued' | 'done'
    error         TEXT,
    duration_ms   INTEGER
);

CREATE INDEX audit_log_ts_idx ON audit_log(ts DESC);
CREATE INDEX audit_log_user_idx ON audit_log(user_sub, ts DESC);
CREATE INDEX audit_log_scope_idx ON audit_log(scope_kind, scope_id, ts DESC);
