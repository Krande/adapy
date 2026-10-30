-- 046_asset_apply_cursors.sql -- where a provider's "apply changes" run asks "since" from.
--
-- A provider that can answer "what changed since a cursor" per element declares an
-- `asset_schedules` entry of kind `apply-changes` (docs/design/apply-changes.md). Each firing asks,
-- per collection, for the changes since the cursor stored here, applies the patches the answer
-- stages into the provider store, and only then stores the answer's `next_cursor`. CORE owns the
-- cursor, not the provider: the provider is stateless about who asked what, and a run that fails
-- part-way must ask the same question again -- which it does, because the row did not move.
--
-- `cursor` is opaque (the E3D provider sends an ISO date); it is compared by nobody, only sent back.
-- No row is a baseline: the next run sends `since: null` and the provider answers "from now". An
-- admin resets a collection by deleting its row. `run_id` is the sweep run (036) that applied it.
-- Collections are stored lower-case, as the attribute index and the grants (045) store them.

CREATE TABLE IF NOT EXISTS asset_apply_cursors (
    provider    TEXT NOT NULL,
    collection  TEXT NOT NULL,
    cursor      TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    run_id      UUID,
    PRIMARY KEY (provider, collection),
    CONSTRAINT asset_apply_cursors_collection_lower CHECK (collection = lower(collection))
);
