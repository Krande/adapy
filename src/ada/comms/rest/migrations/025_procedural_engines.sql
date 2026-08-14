-- Per-scope procedural-engine registry: one row per pluggable procedural
-- modelling engine the cellbuilder can compile with. The doc (JSONB) holds the
-- engine manifest (validated against ada.comms.rest.catalog on commit): its
-- ``kind`` (builtin | wheel | server), the git repo + ref an external engine is
-- built from, the dotted ``entrypoint`` callable (``compile(doc) -> bytes``), the
-- pyodide-installable dep list, and a Vault-backed deploy-key SECRET NAME (never
-- the key itself). ``wheel_key`` (set by the engine-build worker) points at the
-- built pure-python wheel under the hidden _engines/ prefix. The built-in
-- ``adapy-default`` engine needs no row — it is unioned in by the API.
CREATE TABLE IF NOT EXISTS procedural_engines (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    scope_kind  TEXT NOT NULL,
    scope_id    TEXT,
    slug        TEXT NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    doc         JSONB NOT NULL DEFAULT '{"kind": "builtin"}'::jsonb,
    revision    INTEGER NOT NULL DEFAULT 0,
    created_by  TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived    BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE UNIQUE INDEX IF NOT EXISTS procedural_engines_scope_slug
    ON procedural_engines (scope_kind, COALESCE(scope_id, ''), slug)
    WHERE NOT archived;
