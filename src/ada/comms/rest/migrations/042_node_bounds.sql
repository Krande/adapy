-- 042_node_bounds.sql -- per-node world bounds of a published collection, for "what is in this box".
--
-- A provider that knows where its nodes are stages `node-bounds.ndjson.gz`
-- (`ada.assets/node-bounds@1`) beside whatever else it stages; the publish job ingests it here, the
-- way it ingests the attribute index (037). One row per node that HAS bounds: an axis-aligned box in
-- the provider's model frame (Z up, the units of the geometry it delivers), on every level the
-- provider bounds -- a parent's box is the union of its children's -- so a box query can answer at
-- any depth. A node with no geometry has no row.
--
-- `node_bounds_sets` is one row per (scope, provider, collection): the revision and frame of the
-- rows, replaced whole by the next ingest (its rows go with it, by the cascade).
--
-- The GiST index is on the plan-view box (x, y), which the built-in `box` type answers with `&&`
-- (overlaps) and `<@` (contained); z is filtered on the rows it narrows to. No extension needed.

CREATE TABLE IF NOT EXISTS node_bounds_sets (
    scope        TEXT NOT NULL,
    provider     TEXT NOT NULL,
    collection   TEXT NOT NULL,
    revision     TEXT,
    rows         INT NOT NULL DEFAULT 0,
    frame        JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (scope, provider, collection)
);

CREATE TABLE IF NOT EXISTS node_bounds (
    scope       TEXT NOT NULL,
    provider    TEXT NOT NULL,
    collection  TEXT NOT NULL,
    node_id     TEXT COLLATE "C" NOT NULL,
    parent_id   TEXT COLLATE "C",
    -- The published subject a request / load of this node goes through (a site, for a provider
    -- that publishes per site); NULL for a node above every subject.
    subject     TEXT,
    label       TEXT,
    full_name   TEXT,
    kind        TEXT,
    depth       INT,
    min_x       DOUBLE PRECISION NOT NULL,
    min_y       DOUBLE PRECISION NOT NULL,
    min_z       DOUBLE PRECISION NOT NULL,
    max_x       DOUBLE PRECISION NOT NULL,
    max_y       DOUBLE PRECISION NOT NULL,
    max_z       DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (scope, provider, collection, node_id),
    FOREIGN KEY (scope, provider, collection) REFERENCES node_bounds_sets (scope, provider, collection) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS node_bounds_plan_idx
    ON node_bounds USING gist (box(point(min_x, min_y), point(max_x, max_y)));
CREATE INDEX IF NOT EXISTS node_bounds_parent_idx ON node_bounds (scope, provider, collection, parent_id);
