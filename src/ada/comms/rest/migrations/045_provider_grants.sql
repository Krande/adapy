-- 045_provider_grants.sql -- which scope may read which provider dataset.
--
-- Provider data (a provider's published assets, its attribute-index partitions, its change feed)
-- is kept ONCE, in the provider store (the `providers` scope), whichever scope a job was started
-- from. A scope reads it through a GRANT: an admin maps (target scope -> provider, collection) and
-- what the grant allows. Nothing is copied into the target scope, so a re-extraction is seen by
-- every granted scope at once and there is one copy of the data to keep current.
--
-- A grant decides WHO may read, unlike an attribute share (039/040), which only decides WHERE a
-- partition is read and still requires the reader to reach the origin scope. Readers of the target
-- scope (its members, by `can_access`) read what is granted to it; nobody reads the provider store
-- directly but an admin.
--
-- `collection` NULL is every collection of the provider, the ones published later included.
-- Collections are stored lower-case, as the attribute index stores them.
--
-- The three permissions are separate on purpose:
--   attributes -- the provider's attribute-index partitions (search, keys, values, export attrs);
--   geometry   -- its published blobs: trees, manifests, CSG/mesh artefacts, builds of them;
--   request    -- members may ask the provider for the dataset (collection / node / tree requests).
--                 The job still runs as a provider-store job and publishes into the provider store.
--
-- `target_scope` is a storage prefix (`shared`, `projects/<uuid>`, `users/<sub>`), as everywhere
-- else in the database. Not a corpus, and never the provider store itself.

CREATE TABLE IF NOT EXISTS provider_grants (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    target_scope  TEXT NOT NULL,
    provider      TEXT NOT NULL,
    collection    TEXT,
    attributes    BOOLEAN NOT NULL DEFAULT TRUE,
    geometry      BOOLEAN NOT NULL DEFAULT TRUE,
    request       BOOLEAN NOT NULL DEFAULT FALSE,
    granted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    granted_by    TEXT,
    note          TEXT,
    CONSTRAINT provider_grants_collection_lower CHECK (collection IS NULL OR collection = lower(collection)),
    CONSTRAINT provider_grants_not_store CHECK (target_scope <> 'providers' AND target_scope NOT LIKE 'corpus/%')
);

-- One grant per (target, provider, collection | all). COALESCE so the provider-wide grant is unique
-- too (NULLs are distinct in a plain unique index).
CREATE UNIQUE INDEX IF NOT EXISTS provider_grants_identity
    ON provider_grants (target_scope, provider, COALESCE(collection, ''));

-- The read path's lookup: "what is granted to this scope".
CREATE INDEX IF NOT EXISTS provider_grants_target_idx ON provider_grants (target_scope);
-- The admin's view from the other side: "who may read this dataset".
CREATE INDEX IF NOT EXISTS provider_grants_dataset_idx ON provider_grants (provider, collection);
