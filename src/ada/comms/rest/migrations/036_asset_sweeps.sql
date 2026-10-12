-- 036_asset_sweeps.sql -- provider sweeps: one request per collection, each published, every phase timed.
--
-- A provider may declare an `asset_schedules` entry of kind `sweep`. Firing it (from its
-- schedule, or once from Admin -> Providers) asks the provider for EVERY collection it offers,
-- one job each, and publishes what each staged -- the same two jobs the Sources tab runs for one
-- collection, driven by the API instead of a browser, because a scheduled sweep has nobody's
-- browser to drive it.
--
-- `asset_sweep_runs` is one row per firing; `asset_sweep_items` one row per collection in it.
-- An item's `timings` holds what each phase took: the provider's own phases as its summary
-- reports them (`provider`, e.g. login / export / stage), core's derive and write as the publish
-- summary reports them (`publish`), and the wall-clock the API saw (`queued_ms`, `fetch_ms`,
-- `publish_ms`, `total_ms`). Nothing here is aggregated: a sweep is a measurement first.
--
-- `heartbeat_at` is how a sweep whose API process died is told from one still going: the
-- driver stamps it while it runs, and a listing reads a `running` sweep with an old heartbeat
-- as `interrupted`. Unlike clearing every `running` row at boot, that is right with more than
-- one API replica: one replica restarting must not end a sweep another is driving.

CREATE TABLE IF NOT EXISTS asset_sweep_runs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope         TEXT NOT NULL,
    provider      TEXT NOT NULL,
    plugin_id     TEXT NOT NULL,
    job           TEXT NOT NULL,
    settings      JSONB,
    schedule_id   UUID,
    requested_by  TEXT,
    requested_via TEXT NOT NULL,                     -- 'schedule' | 'user'
    status        TEXT NOT NULL DEFAULT 'running',   -- running | done | error | cancelled | interrupted
    total         INT NOT NULL DEFAULT 0,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    heartbeat_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at   TIMESTAMPTZ,
    error         TEXT
);

CREATE INDEX IF NOT EXISTS asset_sweep_runs_created_idx ON asset_sweep_runs (created_at DESC);
CREATE INDEX IF NOT EXISTS asset_sweep_runs_running_idx ON asset_sweep_runs (provider) WHERE status = 'running';

CREATE TABLE IF NOT EXISTS asset_sweep_items (
    run_id          UUID NOT NULL REFERENCES asset_sweep_runs (id) ON DELETE CASCADE,
    position        INT NOT NULL,
    collection      TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending', -- pending | fetching | publishing | done | unchanged | nothing | error | skipped
    fetch_job_id    TEXT,
    publish_job_id  TEXT,
    revision        TEXT,
    message         TEXT,
    error           TEXT,
    timings         JSONB,
    started_at      TIMESTAMPTZ,
    finished_at     TIMESTAMPTZ,
    PRIMARY KEY (run_id, position)
);
