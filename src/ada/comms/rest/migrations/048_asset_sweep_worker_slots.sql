-- 048_asset_sweep_worker_slots.sql -- several runs of one provider at once, sharing its workers.
--
-- A provider's runs (sweeps, apply-changes, collection requests) no longer exclude each other: two
-- may run side by side, over different collections. Two things keep that sane, both decided in
-- the database so they hold across API replicas (routes/asset_sweeps.py):
--
-- * ONE COLLECTION, ONE RUN AT A TIME. A run claims a collection by moving its item to `fetching`
--   under a transaction-scoped advisory lock keyed by (provider, collection); a claim is refused
--   while another live run's item of that collection is `fetching` or `publishing`. The refused
--   item waits (`waiting`) and is tried again; one still refused after a bound ends `busy`.
-- * A SHARED WORKER BUDGET. Before a run enqueues a provider job it takes a slot -- `worker_slot`
--   on its item -- under an advisory lock keyed by the provider, only while fewer items of the
--   provider's live runs hold one than the provider's cap; it gives the slot back when the job is
--   done (publishes run on core's own workers and take none). A run whose heartbeat went stale
--   (its API process died) holds nothing, whatever its rows still say.

ALTER TABLE asset_sweep_items ADD COLUMN IF NOT EXISTS worker_slot BOOLEAN NOT NULL DEFAULT FALSE;

CREATE INDEX IF NOT EXISTS asset_sweep_items_in_flight_idx
    ON asset_sweep_items (lower(collection)) WHERE status IN ('fetching', 'publishing') OR worker_slot;
