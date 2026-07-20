-- 007_corpora.sql — admin-curated regression corpora.
--
-- A corpus is a named collection of "proprietary" or representative
-- source files admins use to drive M3 audit sweeps. Each corpus
-- lives in its own scope (``corpus:<slug>``) — the Storage layer
-- automatically gets ``corpus/<slug>/`` as the bucket prefix once
-- ``ScopeKind`` learns the new value (see Scope.prefix in
-- src/ada/comms/rest/scope.py).
--
-- Corpora are admin-only on every axis: list, read, write, audit
-- dispatch. The ``corpora`` table itself owns the metadata
-- (slug → human name + description); per-file metadata (e.g. the
-- ``expected_to_fail`` annotation called out in
-- the admin audit-panel design notes) lives as a sidecar JSON in
-- the bucket alongside the file, so adding it doesn't require a
-- table migration.
--
-- Slug instead of pure UUID for the wire format: ``corpus:cad-
-- baseline`` is more readable in the admin panel + the audit
-- runs table than ``corpus:7f3e...``. UUID is the FK-stable id.

CREATE TABLE IF NOT EXISTS corpora (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- URL-safe identifier. Lowercase, hyphen-separated; unique
    -- across non-archived rows so the admin can re-claim a slug
    -- after archiving an old corpus of the same name.
    slug          TEXT NOT NULL,
    name          TEXT NOT NULL,
    description   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by    TEXT,
    -- Soft delete — the storage bucket may still hold the corpus
    -- files (operator wipes those separately if needed), and we
    -- want the slug to be reusable once archived.
    archived_at   TIMESTAMPTZ
);

-- Slug uniqueness is only enforced on live corpora — once a row
-- is archived its slug becomes available again for a new corpus.
-- Partial unique index instead of a plain UNIQUE constraint so
-- the archive-and-recreate flow works without a separate
-- rename-on-delete step.
CREATE UNIQUE INDEX IF NOT EXISTS corpora_slug_live_idx
    ON corpora (slug)
    WHERE archived_at IS NULL;

-- Admin-panel listing orders by created_at desc.
CREATE INDEX IF NOT EXISTS corpora_created_at_idx
    ON corpora (created_at DESC);
