-- 039_attr_shares.sql -- one scope's attribute-index partition, searchable from another scope.
--
-- A collection is indexed once, into the scope its provider published it in (037). A team that
-- works in several scopes -- a project and the shared scope, a project and someone's personal
-- scope -- would otherwise have to publish the same collection into each one to search it there,
-- and keep every copy current. A share is a REFERENCE instead: the partition stays where it was
-- ingested, and a search of `target_scope` also reads it. Nothing is copied, so a reingest of the
-- origin is seen in every scope it is shared with at once.
--
-- A share widens WHERE a partition is read, never WHO may read it: a search reads a shared
-- partition only while the caller can still reach its ORIGIN scope (db/attribute_index.py,
-- `readable_share_origins`). Leaving a project hides its shares everywhere, with no row to clean
-- up. Dropping the partition drops its shares with it (the cascade); a reingest keeps them, since
-- it updates the catalogue row rather than replacing it.
--
-- `scope` / `target_scope` are storage prefixes (`shared`, `projects/<uuid>`, `users/<sub>`), as
-- `attr_collections.scope` is. The index on `target_scope` is the search's lookup: "what is shared
-- into this scope".

CREATE TABLE IF NOT EXISTS attr_shares (
    scope         TEXT NOT NULL,
    provider      TEXT NOT NULL,
    collection    TEXT NOT NULL,
    target_scope  TEXT NOT NULL,
    shared_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    shared_by     TEXT,
    PRIMARY KEY (scope, provider, collection, target_scope),
    FOREIGN KEY (scope, provider, collection) REFERENCES attr_collections (scope, provider, collection) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS attr_shares_target_idx ON attr_shares (target_scope);
