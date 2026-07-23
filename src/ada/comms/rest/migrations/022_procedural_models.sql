-- Procedural cell models: one row per model, the whole entity document as
-- JSONB (spaces/equipments/openings validated against the ada.topology
-- pydantic models on commit) with an optimistic-concurrency revision.
CREATE TABLE IF NOT EXISTS procedural_models (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_kind  TEXT NOT NULL,
    scope_id    TEXT,
    name        TEXT NOT NULL,
    doc         JSONB NOT NULL DEFAULT '{"grid": {}, "spaces": [], "equipments": [], "openings": []}'::jsonb,
    revision    INTEGER NOT NULL DEFAULT 0,
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived    BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE UNIQUE INDEX IF NOT EXISTS procedural_models_scope_name
    ON procedural_models (scope_kind, COALESCE(scope_id, ''), name)
    WHERE NOT archived;
