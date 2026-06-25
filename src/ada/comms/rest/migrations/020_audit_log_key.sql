-- 020_audit_log_key.sql — captured-output blob pointer per conversion.
--
-- Every conversion now ships its child stdout+stderr (Python logging AND the
-- adacpp/OCCT C++ libraries' output) to a per-job log blob, so a silently
-- swallowed warning (e.g. "meshopt compression skipped") is recoverable instead
-- of vanishing. ``log_key`` points at that blob (``_derived/<src>.<job>.log``,
-- gzip-at-rest), mirroring ``profile_key``.

ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS log_key text;
