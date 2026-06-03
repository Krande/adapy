-- 010_profile_hotspots.sql — pre-aggregated function-level hotspots
-- pulled from each conversion's cProfile dump.
--
-- The convert worker has long stored a ``.prof`` blob per job when
-- ``profile_conversions`` was enabled (audit_log.profile_key points
-- at it). Parsing those blobs at query time to power the perf
-- dashboard's "what's the hot function in .ifc → .glb conversions?"
-- view is too slow for an interactive UI — pstats reads a single
-- blob in tens of ms but multiplying by N audit rows in a cell over
-- a 30-day window quickly hits seconds.
--
-- Instead a background processor in the API replica peels the top
-- functions out of each new ``.prof`` (by cumtime) and lands them
-- here, one row per (audit_id, rank). The hotspots endpoint then
-- joins back to audit_log for the (source_ext, target_format)
-- filter + GROUPs by (func, file, line) to merge across runs.

-- Only the TOP K functions per profile are stored. K is chosen at
-- write time (see app.py's background processor; current value is
-- 50) — enough to catch the dominant call chain in real conversion
-- profiles without inflating the table to thousands of rows per
-- run. The long tail is droppable: it doesn't shape the streaming
-- conclusion.
CREATE TABLE IF NOT EXISTS profile_function_stats (
    audit_id        BIGINT NOT NULL REFERENCES audit_log(id) ON DELETE CASCADE,
    -- Position within this profile's top-K (0 = the function with
    -- the largest cumulative time). Surfaced in the API so the UI
    -- can show "this is rank 3 in its own run" alongside the
    -- cross-run aggregate.
    rank            SMALLINT NOT NULL,
    func            TEXT NOT NULL,
    file            TEXT NOT NULL,
    line            INTEGER NOT NULL,
    ncalls          BIGINT NOT NULL,
    primitive_calls BIGINT NOT NULL,
    tottime         DOUBLE PRECISION NOT NULL,
    cumtime         DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (audit_id, rank)
);

-- The aggregation query is GROUP BY (func, file, line). Index keeps
-- the GROUP BY cheap even when the cell-by-cell join feeds it
-- thousands of rows.
CREATE INDEX IF NOT EXISTS profile_function_stats_func_idx
    ON profile_function_stats (func, file, line);

-- Per-audit_log housekeeping for the processor:
-- ``profile_stats_processed_at`` flips when the background parser
-- has finished with a row (success or failure). NULL means "needs
-- work"; the parser's ``WHERE`` clause filters on this so finished
-- rows are skipped without revisiting them.
ALTER TABLE audit_log ADD COLUMN profile_stats_processed_at TIMESTAMPTZ;
ALTER TABLE audit_log ADD COLUMN profile_stats_error TEXT;

-- Partial index over the small subset the parser scans every tick.
-- Without this, the parser's claim query (``profile_key NOT NULL
-- AND processed_at IS NULL``) ends up doing a seq scan whenever a
-- big audit run lands a few hundred rows at once.
CREATE INDEX IF NOT EXISTS audit_log_profile_pending_idx
    ON audit_log (id)
    WHERE profile_key IS NOT NULL AND profile_stats_processed_at IS NULL;
