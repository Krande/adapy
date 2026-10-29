-- 044_node_relations.sql -- typed, directed relations between the nodes of a published collection.
--
-- A provider that knows how its nodes relate beyond the tree (a piece that fills an opening, that
-- serves a system, that connects to another element) stages `node-relations.ndjson.gz`
-- (`ada.assets/node-relations@1`); the publish job ingests it here, the way it ingests node bounds
-- (042). The relation is read by a writer for the nodes it exports, whatever else the export holds.
--
-- `node_relations_sets` is one row per (scope, provider, collection), replaced whole by the next
-- ingest (its rows go with it, by the cascade). `ingested_at` moves with every ingest, so it also
-- tells a cached export that its relations changed.

CREATE TABLE IF NOT EXISTS node_relations_sets (
    scope        TEXT NOT NULL,
    provider     TEXT NOT NULL,
    collection   TEXT NOT NULL,
    revision     TEXT,
    rows         INT NOT NULL DEFAULT 0,
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (scope, provider, collection)
);

CREATE TABLE IF NOT EXISTS node_relations (
    scope           TEXT NOT NULL,
    provider        TEXT NOT NULL,
    collection      TEXT NOT NULL,
    from_node_id    TEXT COLLATE "C" NOT NULL,
    from_full_name  TEXT COLLATE "C",
    relation        TEXT NOT NULL,
    to_node_id      TEXT COLLATE "C",
    to_full_name    TEXT,
    props           JSONB NOT NULL DEFAULT '{}'::jsonb,
    FOREIGN KEY (scope, provider, collection)
        REFERENCES node_relations_sets (scope, provider, collection) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS node_relations_from_idx ON node_relations (scope, collection, from_node_id);
CREATE INDEX IF NOT EXISTS node_relations_from_name_idx ON node_relations (scope, collection, from_full_name);
