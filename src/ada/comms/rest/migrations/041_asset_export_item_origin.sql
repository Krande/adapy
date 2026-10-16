-- 041_asset_export_item_origin.sql -- the scope an export item's hits are READ from.
--
-- A search of one scope also reads partitions that lie in another (shared into it, 039/040, or an
-- admin's search of every scope). An export of such hits reads their attribute rows, tree and
-- geometry where the partition lies -- the ORIGIN -- and writes the files where the export was
-- asked to, as before. One item is one (origin, provider, collection) batch, so the origin is the
-- item's: NULL is the run's own scope (`asset_export_runs.scope`), which is every item written
-- before this column existed. A storage prefix, like the run's scope.

ALTER TABLE asset_export_items ADD COLUMN IF NOT EXISTS origin_scope TEXT;
