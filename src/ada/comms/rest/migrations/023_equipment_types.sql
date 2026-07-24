-- Per-scope equipment-type catalog: one row per reusable equipment archetype
-- (pump/tank/...). The doc (JSONB) holds the placement-independent definition —
-- bounding box, mass, IFC element class and the port/nozzle list (validated
-- against ada.comms.rest.catalog on commit). ``cad_key`` optionally links a
-- CAD/GLB asset (under the hidden _equipment/ prefix) from which the bbox and a
-- preview GLB are inferred by the equipment_bbox worker job. The catalog feeds
-- the cellbuilder's add-equipment dropdown (by slug).
CREATE TABLE IF NOT EXISTS equipment_types (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_kind  TEXT NOT NULL,
    scope_id    TEXT,
    slug        TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    doc         JSONB NOT NULL DEFAULT '{"bbox": {"lx": 1.0, "ly": 1.0, "lz": 1.0}, "mass": 1000.0, "ifc_element_class": "IfcBuildingElementProxy", "ports": []}'::jsonb,
    cad_key     TEXT,
    revision    INTEGER NOT NULL DEFAULT 0,
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived    BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE UNIQUE INDEX IF NOT EXISTS equipment_types_scope_slug
    ON equipment_types (scope_kind, COALESCE(scope_id, ''), slug)
    WHERE NOT archived;
