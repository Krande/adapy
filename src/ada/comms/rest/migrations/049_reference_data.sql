-- 049_reference_data.sql -- what a provider's collection OFFERS for a new element to point at.
--
-- A provider that can list its source's catalogue (every spec and spec component a section, plate
-- or fitting reference may take, every material) stages `reference-data.ndjson.gz`
-- (`ada.assets/reference-data@1`); the publish job ingests it here, the way it ingests node
-- relations (044). Unlike the attribute index it describes no node: an entry is listed whether any
-- element uses it or not -- the point is to pick one nobody uses yet. How many elements DO use an
-- entry is read from the attribute index at query time, never stored here.
--
-- `reference_sets` is one row per (scope, provider, collection), replaced whole by the next ingest
-- (its rows go with it, by the cascade). Provider data lands in the provider store (`providers`)
-- and a scope reads it through an `attributes` grant (045), like the attribute index.
--
-- `ref` is the entry's name exactly as an element's reference attribute holds it (what a macro
-- writes after SPRE or MATR); with `kind` it is the row's identity. `use` is what the provider
-- says the entry is for (section, plate, fitting, joint, material, other).

CREATE TABLE IF NOT EXISTS reference_sets (
    scope        TEXT NOT NULL,
    provider     TEXT NOT NULL,
    collection   TEXT NOT NULL,
    revision     TEXT,
    rows         INT NOT NULL DEFAULT 0,
    counts       JSONB NOT NULL DEFAULT '{}'::jsonb,
    ingested_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (scope, provider, collection)
);

CREATE TABLE IF NOT EXISTS reference_items (
    scope       TEXT NOT NULL,
    provider    TEXT NOT NULL,
    collection  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    ref         TEXT COLLATE "C" NOT NULL,
    name        TEXT,
    owner       TEXT COLLATE "C",
    use         TEXT,
    attrs       JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (scope, provider, collection, kind, ref),
    FOREIGN KEY (scope, provider, collection)
        REFERENCES reference_sets (scope, provider, collection) ON DELETE CASCADE
);

-- The browser's filters: by use, and the members of one spec (`owner`). A ref prefix uses the
-- primary key (C collation).
CREATE INDEX IF NOT EXISTS reference_items_use_idx ON reference_items (scope, provider, collection, use, ref);
CREATE INDEX IF NOT EXISTS reference_items_owner_idx ON reference_items (scope, provider, collection, owner);
