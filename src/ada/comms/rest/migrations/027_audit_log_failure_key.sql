-- 027_audit_log_failure_key.sql — pointer to the preserved copy of a failed job's source.
--
-- A failed conversion is only reproducible while its source blob still exists, and a
-- user-scope source can be deleted (or expire) at any time — the audit row outlives the
-- bytes it refers to, so `GET /admin/audit/{id}/source` starts 404ing on exactly the rows
-- worth investigating. Capture-at-failure copies the source into the admin-only failure
-- corpus while it is still there; ``failure_key`` is where that copy landed.
--
-- Content-addressed on the source's storage identity, so one blob is stored once no matter
-- how many rows point at it: this column is a many-to-one pointer, not an owner.

ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS failure_key text;
