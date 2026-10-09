-- 035_asset_schedules.sql — provider-declared schedules, and a record of every change check.
--
-- An asset provider now declares the jobs it offers for scheduling on its plugin spec
-- (`asset_schedules`, next to `asset_collection_request` and friends). An admin sets them up
-- per scope and collection on Admin → Providers, from dropdowns; the scheduler that already
-- runs `plugin_job_schedules` fires them. These columns are what ties a schedule row back to
-- the provider entry it was made from, so the Providers tab can list its own and rebuild the
-- job options from the live declaration rather than trusting free text. A row with
-- `asset_provider` NULL is a plain plugin-job schedule from before this existed.
--
-- `asset_change_runs` is one row per change check, scheduled or started from the Sources tab.
-- The job's summary carries the provider-neutral `asset_changes` block; the API reads it once
-- the job is done and copies the headline here, so the run list needs no blob reads. The item
-- list stays where the provider wrote it (`items_key`, in the scope's storage) unless it was
-- small enough to come back inline.

ALTER TABLE plugin_job_schedules
    ADD COLUMN IF NOT EXISTS asset_provider TEXT,
    ADD COLUMN IF NOT EXISTS collection     TEXT,
    ADD COLUMN IF NOT EXISTS schedule_job   TEXT,
    ADD COLUMN IF NOT EXISTS schedule_kind  TEXT;

CREATE INDEX IF NOT EXISTS plugin_job_schedules_asset_provider_idx
    ON plugin_job_schedules (asset_provider) WHERE asset_provider IS NOT NULL;

CREATE TABLE IF NOT EXISTS asset_change_runs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope         TEXT NOT NULL,
    provider      TEXT NOT NULL,
    collection    TEXT NOT NULL,
    plugin_id     TEXT NOT NULL,
    schedule_id   UUID,
    job_id        TEXT,
    derived_key   TEXT,
    requested_by  TEXT,
    requested_via TEXT NOT NULL,           -- 'schedule' | 'user'
    status        TEXT NOT NULL DEFAULT 'queued',  -- queued | done | error
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ,
    stale         BOOLEAN,
    up_to_date    BOOLEAN,
    message       TEXT,
    counts        JSONB,
    users         JSONB,
    since         TEXT,
    checked_at    TEXT,
    items_key     TEXT,
    items         JSONB,
    error         TEXT
);

CREATE INDEX IF NOT EXISTS asset_change_runs_lookup_idx
    ON asset_change_runs (scope, provider, collection, created_at DESC);
CREATE INDEX IF NOT EXISTS asset_change_runs_pending_idx
    ON asset_change_runs (status) WHERE status = 'queued';
