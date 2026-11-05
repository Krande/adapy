-- 054_user_preferences_fk.sql — a user's preferences go with the user.
--
-- Same rule as ``project_members``: ``user_sub`` references ``users(sub)`` with ON DELETE
-- CASCADE, so deleting a ``users`` row deletes that user's preference documents.
-- ``set_user_preference`` inserts a placeholder ``users`` row in the same transaction when the
-- sub has not been seen yet (as ``add_project_member`` does), so the key always holds.
--
-- Rows written under 053 for a sub with no ``users`` row get one first, rather than being
-- dropped.

INSERT INTO users (sub)
SELECT DISTINCT user_sub FROM user_preferences
ON CONFLICT (sub) DO NOTHING;

ALTER TABLE user_preferences
    ADD CONSTRAINT user_preferences_user_sub_fkey
    FOREIGN KEY (user_sub) REFERENCES users(sub) ON DELETE CASCADE;
