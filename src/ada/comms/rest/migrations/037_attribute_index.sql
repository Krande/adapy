-- 037_attribute_index.sql -- the provider-neutral attribute index behind `POST .../assets/search`.
--
-- A provider job that knows its nodes' attributes stages `attribute-index.ndjson.gz`
-- (`ada.assets/attribute-index@1`) beside whatever else it stages; the publish job ingests it here
-- (formats/asset_publish.py, db/attribute_index.py). One question this answers well: "every node of
-- depth 3 with TYPE=X, FUNC=Y, PURP in (A, B)", across every collection of a scope, in one call.
--
-- `attr_nodes` is LIST-partitioned on `part_key`, a hash of (scope, provider, collection): one
-- partition per collection a provider publishes into a scope. A reingest never updates rows in
-- place -- it builds a fresh table, COPYs into it, indexes it, and then, in ONE transaction,
-- detaches and drops the old partition and attaches the new one under the same name. A reader sees
-- the old collection or the new one, never half of each, and never waits on a load. Every
-- per-partition index (GIN jsonb_path_ops on attrs, btree on node_id / full_name / (kind, depth))
-- is built on the standalone table before it is attached, so the parent carries none.
--
-- The partition holds no scope / provider / collection columns: they are the same on every row of
-- it, and `attr_collections` (one row per partition) says them once. node_id, parent_id and
-- full_name are COLLATE "C": byte order is the keyset-pagination order, and a btree in C order
-- also serves `LIKE 'prefix%'`.
--
-- `attr_values` is a per-partition value histogram computed at ingest for each key whose distinct
-- values stay few (a key with too many -- names, descriptions -- is listed in
-- `attr_collections.high_card_keys` and answered by a scan instead), so value autocomplete is a
-- primary-key range read rather than a pass over every node of the collection.

CREATE TABLE IF NOT EXISTS attr_nodes (
    part_key    TEXT NOT NULL,
    node_id     TEXT COLLATE "C" NOT NULL,
    parent_id   TEXT COLLATE "C",
    full_name   TEXT COLLATE "C",
    source_ref  TEXT,
    kind        TEXT,
    depth       INT,
    attrs       JSONB NOT NULL DEFAULT '{}'::jsonb
) PARTITION BY LIST (part_key);

CREATE TABLE IF NOT EXISTS attr_collections (
    scope           TEXT NOT NULL,
    provider        TEXT NOT NULL,
    collection      TEXT NOT NULL,
    revision        TEXT,
    rows            BIGINT NOT NULL DEFAULT 0,
    source_etag     TEXT,
    depth_base      TEXT,
    ingested_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    partition_name  TEXT NOT NULL,
    part_key        TEXT NOT NULL UNIQUE,
    keys            TEXT[] NOT NULL DEFAULT '{}',
    high_card_keys  TEXT[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (scope, provider, collection)
);

CREATE TABLE IF NOT EXISTS attr_values (
    part_key  TEXT NOT NULL,
    key       TEXT COLLATE "C" NOT NULL,
    value     TEXT COLLATE "C" NOT NULL,
    n         BIGINT NOT NULL,
    PRIMARY KEY (part_key, key, value)
);
