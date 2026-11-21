-- 055_element_links.sql -- per-element associations (ada.assoc): what has been found, built and
-- simulated for each model element, filed under the element and its ancestors.
--
-- A projection of the producing documents (clash runs, detail models, subject files, simulation run
-- records), mirrored from the files backend (`_assoc/` in each scope's storage) and rebuildable from
-- them by a reindex. One row per element per fact: a contact gives two rows, a connection one per
-- member. A row is UNDER an element X when its element_key is X or X is in its `anc`.

CREATE TABLE IF NOT EXISTS element_links (
    id               BIGSERIAL PRIMARY KEY,
    scope            TEXT NOT NULL,
    kind             TEXT NOT NULL,
    element_key      TEXT NOT NULL COLLATE "C",
    anc              TEXT[] NOT NULL DEFAULT '{}',
    -- The producing document: a writer replaces every row of (scope, doc_key, kind) at once.
    doc_key          TEXT NOT NULL,
    ck               TEXT,
    -- What a listing groups by and pages on: the ck, or a contact's member pair.
    grp              TEXT NOT NULL COLLATE "C",
    source           TEXT,
    revision         TEXT,
    geometry_sha256  TEXT,
    fp               TEXT,
    created_at       TEXT,
    status           TEXT,
    data             JSONB NOT NULL DEFAULT '{}'::jsonb,
    written_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS element_links_element ON element_links (scope, element_key, kind);
CREATE INDEX IF NOT EXISTS element_links_anc ON element_links USING GIN (anc);
CREATE INDEX IF NOT EXISTS element_links_ck ON element_links (scope, ck, kind);
CREATE INDEX IF NOT EXISTS element_links_doc ON element_links (scope, doc_key, kind);
CREATE INDEX IF NOT EXISTS element_links_grp ON element_links (scope, grp);
CREATE INDEX IF NOT EXISTS element_links_active_runs ON element_links (scope, status)
    WHERE kind = 'sim_run' AND status IN ('queued', 'running');

-- An element's newest fingerprint, as its source (a target's GLB) was last measured.
CREATE TABLE IF NOT EXISTS element_state (
    scope        TEXT NOT NULL,
    element_key  TEXT NOT NULL COLLATE "C",
    source       TEXT,
    revision     TEXT,
    fp           TEXT,
    seen_at      TEXT NOT NULL,
    PRIMARY KEY (scope, element_key)
);

-- A source's newest snapshot: an element of it whose state is older than this was not in it.
CREATE TABLE IF NOT EXISTS element_sources (
    scope            TEXT NOT NULL,
    source           TEXT NOT NULL,
    revision         TEXT,
    geometry_sha256  TEXT,
    seen_at          TEXT NOT NULL,
    PRIMARY KEY (scope, source)
);

-- "A run of this connection is being started": one claim per connection, provider, parameters and
-- generation, so two presses (two tabs) never queue the same run twice.
CREATE TABLE IF NOT EXISTS sim_intents (
    scope        TEXT NOT NULL,
    ck           TEXT NOT NULL,
    provider     TEXT NOT NULL,
    params_hash  TEXT NOT NULL,
    generation   INTEGER NOT NULL DEFAULT 0,
    run_id       TEXT,
    taken_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (scope, ck, provider, params_hash, generation)
);
