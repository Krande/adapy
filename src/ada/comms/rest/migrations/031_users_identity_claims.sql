-- 031_users_identity_claims.sql — what the admin Users tab can say about a
-- principal beyond its name.
--
-- `users` has recorded sub / email / display_name / last_seen_at since 001.
-- The token carries two more things an operator asks about — "is this person
-- an admin?" and "which IdP groups put them there?" — but both were computed
-- per request and thrown away. They are now written on the same lazy upsert
-- that already runs on `/api/me`. `created_at` is when the row appeared: the
-- first sign-in, or the moment an admin added the sub to a project before its
-- owner ever signed in (add_project_member inserts a placeholder row).
--
-- ALL NULLABLE, AND NULL MEANS "NOT RECORDED". An existing row has no truthful
-- value for any of these: back-filling `is_admin = false` would report every
-- current admin as a non-admin until their next sign-in, and back-filling
-- `created_at = NOW()` would stamp the whole user base with the deploy time.
-- The UI renders NULL as "unknown" instead.
--
-- `created_at` gets its DEFAULT in a separate statement for the same reason:
-- `ADD COLUMN ... DEFAULT NOW()` fills existing rows with the migration's
-- timestamp, while setting the default afterwards only applies it to rows
-- inserted from here on.
ALTER TABLE users ADD COLUMN created_at TIMESTAMPTZ;
ALTER TABLE users ALTER COLUMN created_at SET DEFAULT NOW();
ALTER TABLE users ADD COLUMN is_admin BOOLEAN;
ALTER TABLE users ADD COLUMN groups TEXT[];
