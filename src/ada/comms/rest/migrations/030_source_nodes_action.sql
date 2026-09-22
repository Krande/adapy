-- 030_source_nodes_action.sql — per-node change evidence, borrowed from
-- IfcChangeActionEnum, adopted PARTIALLY (see plan/v4's Decision 7).
--
-- WHAT THIS IS NOT. `source_nodes` already answers four ROOT-level questions
-- ("behind" / "current" / "not-recorded" / "no-feed") from whether a row exists
-- at all and how its `last_changed_at` compares to what was published — that
-- logic reads the table, not this column, and is unchanged by this migration.
--
-- WHAT THIS IS. A writer that swept a source and found a product changed,
-- added or removed now has somewhere to say WHICH of the three it saw, per
-- node — evidence a "changed by" / "what happened" row detail can show without
-- the reader re-deriving it from two timestamps. `IfcChangeActionEnum`'s
-- NOCHANGE / MODIFIEDADDED / MODIFIEDDELETED are deliberately not represented:
-- a row's absence from a sweep already means "no change" (writing NOCHANGE rows
-- would turn this table into a full mirror of the source), and the compound
-- values encode a HISTORY this table does not keep — two revisions of a
-- manifest already say "added, then modified" without a fourth enum value.
--
-- ADDITIVE AND NULLABLE, on purpose. Every existing writer (an out-of-tree
-- provider's feed, any sweep written before this column existed) keeps inserting rows with no
-- opinion about `action`, and every existing reader keeps working without
-- asking for it — a writer that cannot say what happened is not thereby wrong,
-- only silent on this one axis.

-- No IF NOT EXISTS needed on either statement: the runner (`db/migrations.py`)
-- records each filename in `schema_version` and never re-applies one, so this
-- body runs exactly once per database, same as every other migration here.
ALTER TABLE source_nodes ADD COLUMN action TEXT;

ALTER TABLE source_nodes
    ADD CONSTRAINT source_nodes_action_check
    CHECK (action IS NULL OR action IN ('added', 'modified', 'deleted'));
