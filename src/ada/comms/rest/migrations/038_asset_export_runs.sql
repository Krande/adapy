-- 038_asset_export_runs.sql -- search-result exports fanned out over jobs, and "skip current" for runs.
--
-- An attribute search (037) answers "which nodes"; exporting its hits writes one IFC per hit into a
-- folder of a scope the user chose. The hits of one (provider, collection) are one job -- they share
-- a geometry source, an attribute partition and a read of the published spine -- so a run is one
-- `asset_export_runs` row and one `asset_export_items` row per (provider, collection) batch.
--
-- Unlike a provider sweep (036), items run CONCURRENTLY (bounded): an export holds no licensed seat,
-- it reads published blobs, so waiting for one collection before starting the next would only make
-- the run longer. Every item is still timed on its own: `queued_ms` (waiting for a worker), `run_ms`
-- (the job), and the job's own phases (`job`: resolve / read / ifc / attrs / convert / thumb /
-- upload). Per-file outcomes ride in `file_errors`, counts in `files`.
--
-- Heartbeat semantics are the sweeps': a `running` run whose heartbeat is old reads as `interrupted`.
--
-- SKIP CURRENT. Cancel stops a whole run; skip stops ONE item and lets the run carry on with the
-- next. The request is stored durably on the run (`skip_position`), because the driver sees it from
-- its poll loop and a request kept only in memory would be lost by a replica that did not start the
-- run. The driver cancels the item's job, records the item as skipped, and clears the request. The
-- same column is added to `asset_sweep_runs` here (036 is already applied where it matters, so it
-- is extended, never edited); `skip_requested_by` names who asked, for the item's message.

ALTER TABLE asset_sweep_runs ADD COLUMN IF NOT EXISTS skip_position INT;
ALTER TABLE asset_sweep_runs ADD COLUMN IF NOT EXISTS skip_requested_by TEXT;

CREATE TABLE IF NOT EXISTS asset_export_runs (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope              TEXT NOT NULL,                     -- the scope searched (storage prefix: users/<sub>, projects/<id>, shared)
    target_scope       TEXT NOT NULL,                     -- where the files are written (as the user named it)
    target_folder      TEXT NOT NULL,
    query              JSONB,
    options            JSONB,                             -- {include_source_attributes, also}
    hits               INT NOT NULL DEFAULT 0,
    requested_by       TEXT,
    status             TEXT NOT NULL DEFAULT 'running',   -- running | done | error | cancelled | interrupted
    total              INT NOT NULL DEFAULT 0,
    skip_position      INT,
    skip_requested_by  TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    heartbeat_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at        TIMESTAMPTZ,
    error              TEXT
);

CREATE INDEX IF NOT EXISTS asset_export_runs_scope_idx ON asset_export_runs (scope, created_at DESC);

CREATE TABLE IF NOT EXISTS asset_export_items (
    run_id        UUID NOT NULL REFERENCES asset_export_runs (id) ON DELETE CASCADE,
    position      INT NOT NULL,
    provider      TEXT NOT NULL,
    collection    TEXT NOT NULL,
    hits          JSONB NOT NULL,                         -- the batch's hits, as the job receives them
    status        TEXT NOT NULL DEFAULT 'pending',        -- pending | running | done | partial | error | skipped
    job_id        TEXT,
    message       TEXT,
    error         TEXT,
    timings       JSONB,
    files         JSONB,                                  -- {written, no_geometry, failed}
    file_errors   JSONB,                                  -- [{node_id, full_name, status, error}]
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    PRIMARY KEY (run_id, position)
);
