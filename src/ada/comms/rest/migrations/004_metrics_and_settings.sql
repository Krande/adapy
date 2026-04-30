-- 004_metrics_and_settings.sql — per-job resource metrics + global app
-- settings.
--
-- Metrics columns are populated by the conversion worker around each
-- convert() call (psutil-bound CPU / RSS / IO deltas). All nullable so
-- pre-existing rows and non-convert audit actions leave them empty —
-- the admin panel hides the metrics tab when every column is NULL.
--
-- profile_key references the storage path of an optional cProfile
-- output; written only when the profile_conversions setting is on at
-- the time of the run.

ALTER TABLE audit_log ADD COLUMN cpu_user_ms BIGINT;
ALTER TABLE audit_log ADD COLUMN cpu_sys_ms  BIGINT;
ALTER TABLE audit_log ADD COLUMN peak_rss_kb BIGINT;
ALTER TABLE audit_log ADD COLUMN read_bytes  BIGINT;
ALTER TABLE audit_log ADD COLUMN write_bytes BIGINT;
ALTER TABLE audit_log ADD COLUMN profile_key TEXT;

-- Generic per-deployment settings table. Today carries the
-- `profile_conversions` toggle; future global flags (rate limits,
-- feature gates) can land here without another migration.
CREATE TABLE IF NOT EXISTS app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by  TEXT
);
