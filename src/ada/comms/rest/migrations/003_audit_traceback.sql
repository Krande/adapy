-- 003_audit_traceback.sql — store the full Python traceback for failed
-- audit rows so the admin panel can show *why* a conversion failed,
-- not just the one-line message. Nullable: pre-existing rows and
-- non-error rows leave it empty.

ALTER TABLE audit_log ADD COLUMN traceback TEXT;
