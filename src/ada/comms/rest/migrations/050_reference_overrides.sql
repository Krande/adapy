-- 050_reference_overrides.sql -- an admin's values for a reference entry, kept apart from the
-- provider's so a re-export of the reference data (049, replaced whole) never loses them.
--
-- A source catalogue usually says little about a material beyond its name and density; the
-- mechanical properties a model needs (E, Poisson's ratio, yield and ultimate strength, thermal
-- expansion) are entered here, per (provider, collection, kind, ref), and merged over the provider's
-- values on read (`db/reference_data.py`, the catalogue). Not per scope: an entry means the same in
-- every scope that reads the collection. An override of an entry the provider no longer lists stays
-- (it is shown as orphaned) until an admin removes it.

CREATE TABLE IF NOT EXISTS reference_overrides (
    provider    TEXT NOT NULL,
    collection  TEXT NOT NULL,
    kind        TEXT NOT NULL,
    ref         TEXT COLLATE "C" NOT NULL,
    props       JSONB NOT NULL DEFAULT '{}'::jsonb,
    note        TEXT,
    updated_by  TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (provider, collection, kind, ref),
    CONSTRAINT reference_overrides_collection_lower CHECK (collection = lower(collection))
);
