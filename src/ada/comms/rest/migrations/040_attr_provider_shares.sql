-- 040_attr_provider_shares.sql -- every attribute-index partition one provider holds in a scope,
-- searchable from another scope: a share of the GROUP rather than of one collection.
--
-- A per-collection share (039) names one partition, so a collection a later sweep adds to the
-- same (scope, provider) is not shared until someone shares it too. A provider share names the
-- group instead: a search of `target_scope` reads every partition of (scope, provider) -- the
-- ones there now and the ones ingested later -- exactly as if each had its own 039 row.
--
-- The access rule is 039's: a share widens WHERE a partition is read, never WHO may read it (a
-- search reads it only while the caller can reach the ORIGIN scope). No foreign key: the group
-- outlives any one of its collections, and a provider share whose (scope, provider) holds nothing
-- right now simply matches nothing. Removing a provider share leaves the 039 rows of its
-- collections alone -- what was shared one by one stays shared.
--
-- Scopes are storage prefixes, as in 039; the index on `target_scope` is the search's lookup.

CREATE TABLE IF NOT EXISTS attr_provider_shares (
    scope         TEXT NOT NULL,
    provider      TEXT NOT NULL,
    target_scope  TEXT NOT NULL,
    shared_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    shared_by     TEXT,
    PRIMARY KEY (scope, provider, target_scope)
);

CREATE INDEX IF NOT EXISTS attr_provider_shares_target_idx ON attr_provider_shares (target_scope);
