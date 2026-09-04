-- 028_source_nodes.sql — when each node of an external source last changed.
--
-- THE PROBLEM. A model kept in an external CAD system is exported into this
-- viewer as assets, and an asset is a snapshot: correct when it was made and
-- silently wrong afterwards. Nothing here can say whether the source has moved
-- since, so every consumer either re-exports on a schedule it guesses at, or
-- serves stale geometry without knowing. Both are bad, and neither is visible.
--
-- WHAT THIS TABLE IS. One row per node of a source hierarchy, carrying the last
-- time that node — or anything beneath it — changed. That is the whole answer to
-- "is what I hold still current", and it is a single indexed lookup rather than
-- a walk.
--
-- THE ANCESTOR ROLL-UP IS THE WRITER'S JOB, not this schema's. A writer that
-- observes a leaf change stamps the leaf AND every node above it, so a consumer
-- asking about a branch reads one row instead of a recursive query over a
-- hierarchy this database deliberately does not model. `parent_ref` is recorded
-- so a reader can reconstruct shape if it wants to, and is never required to.
--
-- OPAQUE IDENTIFIERS, ON PURPOSE. `node_ref` is whatever the source calls a
-- node, stored verbatim and never parsed here. Different sources have entirely
-- different grammars, and a schema that understood one of them would have to
-- grow a branch for the next. `source` names which vocabulary a ref belongs to
-- so two sources can share a scope without colliding.
--
-- SCOPE-KEYED, like every other per-project table: a source hierarchy belongs to
-- the project it was exported from, and a caller who cannot read the scope must
-- not be able to read its change history either.

CREATE TABLE IF NOT EXISTS source_nodes (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The scope string as the API resolves it, stored rather than FK'd: scopes
    -- include kinds that are not rows in any table (the shared bucket, corpora),
    -- and this table must be usable from all of them.
    scope           TEXT NOT NULL,
    -- Which external source this ref belongs to. Free text, matching the
    -- provider id a plugin registers under.
    source          TEXT NOT NULL,
    node_ref        TEXT NOT NULL,
    parent_ref      TEXT,
    -- Display name, when the writer knows one. Not an identifier: two nodes may
    -- share a name, and a rename must not orphan history.
    name            TEXT,
    -- When this node or anything under it last changed IN THE SOURCE. This is
    -- the column the whole table exists for.
    last_changed_at TIMESTAMPTZ NOT NULL,
    -- Who made that change, when the source records it. Optional because not
    -- every source attributes edits.
    last_changed_by TEXT,
    -- When a writer last confirmed the two columns above. Distinct from
    -- last_changed_at and NOT redundant: "checked ten minutes ago, unchanged
    -- since Tuesday" and "last heard from on Tuesday" are different states, and
    -- only the second is a reason to distrust the answer.
    observed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- One row per node per source per scope. This is the upsert target: a writer
-- re-stamping a node it has seen before must update rather than accumulate, or
-- an hourly job turns into an append-only log of the same tree.
CREATE UNIQUE INDEX IF NOT EXISTS source_nodes_identity
    ON source_nodes (scope, source, node_ref);

-- "What changed since T", the sweep query a consumer polls with.
CREATE INDEX IF NOT EXISTS source_nodes_changed_at
    ON source_nodes (scope, source, last_changed_at DESC);

-- Children of a node, for a reader that does want to walk.
CREATE INDEX IF NOT EXISTS source_nodes_parent
    ON source_nodes (scope, source, parent_ref);
