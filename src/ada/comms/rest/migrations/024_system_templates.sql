-- Per-scope system-template catalog: reusable service-system definitions
-- (a named CoolingWater piping system, a PowerFeed electrical system, ...).
-- The doc (JSONB) holds category/type/medium/voltage and the routed-segment
-- rendering knobs (segment class, pipe radius/wall thickness), validated against
-- ada.comms.rest.catalog on commit. Feeds the cellbuilder systems inspector's
-- "add system" type list (by slug).
CREATE TABLE IF NOT EXISTS system_templates (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_kind  TEXT NOT NULL,
    scope_id    TEXT,
    slug        TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    doc         JSONB NOT NULL DEFAULT '{"type": "piping", "medium": null, "pipe_radius": 0.05, "pipe_wt": 0.005}'::jsonb,
    revision    INTEGER NOT NULL DEFAULT 0,
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived    BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE UNIQUE INDEX IF NOT EXISTS system_templates_scope_slug
    ON system_templates (scope_kind, COALESCE(scope_id, ''), slug)
    WHERE NOT archived;
