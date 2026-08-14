-- Procedural models carry a routing/identity header: which engine compiles the
-- document (a built-in slug or a registered engine's slug) and the doc-schema
-- version it was authored against. Stored as first-class columns (mirrored from
-- doc["engine"] / doc["schema_version"] on every commit) so a compile can route
-- to the right engine + capability worker without parsing the JSONB doc, and so
-- schema drift is queryable. See ada.topo_model.engines.EngineBinding.
ALTER TABLE procedural_models
    ADD COLUMN IF NOT EXISTS engine TEXT NOT NULL DEFAULT 'adapy-default',
    ADD COLUMN IF NOT EXISTS schema_version TEXT NOT NULL DEFAULT '1.0';
