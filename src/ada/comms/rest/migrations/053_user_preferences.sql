-- 053_user_preferences.sql — a signed-in user's own viewer preferences.
--
-- What a user arranges in the viewer (its panel layout first) follows them from browser to
-- browser. One row per (user, key): ``key`` names one preference document, the client owns its
-- shape, and the server stores the JSON as given. Rows are read and written only by their user
-- (``/api/me/preferences/{key}``); no admin view lists them.
--
-- No foreign key to ``users``: that row is upserted lazily by ``/api/me``, and a preference
-- written before it must not fail.

CREATE TABLE IF NOT EXISTS user_preferences (
    user_sub    TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_sub, key)
);
