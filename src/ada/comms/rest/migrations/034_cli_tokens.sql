-- 034_cli_tokens.sql — one row per issued CLI bearer token.
--
-- CLI tokens were stateless JWTs: nothing recorded that one had been issued, so the
-- admin panel could neither say how many were live nor revoke a single one. The only
-- control was the per-subject "revoke all" cutoff in app_settings. Each token now
-- carries a ``jti`` claim and is recorded here at mint time; verification rejects a
-- jti whose row is revoked or missing. The cutoff stays: it still governs tokens
-- issued before this table existed, which carry no jti and so cannot be listed.
--
-- The token itself is never stored. ``hint`` is its last eight characters (the tail of
-- the HMAC signature) — enough to tell two tokens apart in a list, and useless for
-- reconstructing one. The leading characters would not do: every token starts with
-- the same JWT header.

CREATE TABLE IF NOT EXISTS cli_tokens (
    jti           TEXT PRIMARY KEY,
    sub           TEXT NOT NULL,
    email         TEXT,
    display_name  TEXT,
    is_admin      BOOLEAN NOT NULL DEFAULT FALSE,
    label         TEXT,
    hint          TEXT NOT NULL,
    issued_by     TEXT,
    issued_at     TIMESTAMPTZ NOT NULL,
    expires_at    TIMESTAMPTZ NOT NULL,
    last_used_at  TIMESTAMPTZ,
    revoked_at    TIMESTAMPTZ,
    revoked_by    TEXT
);

CREATE INDEX IF NOT EXISTS cli_tokens_sub_idx ON cli_tokens (sub);
CREATE INDEX IF NOT EXISTS cli_tokens_expires_at_idx ON cli_tokens (expires_at);
