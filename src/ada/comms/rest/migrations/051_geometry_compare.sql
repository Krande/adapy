-- Geometry comparison of two providers' tessellations of the same elements (docs/design/geometry-compare.md).
--
-- One row per (first provider, second provider, collection, element key): the CURRENT verdict on that
-- element, replaced by every comparison run of the collection. It is the gallery's source (failed and
-- uncertain rows, with their pictures) and the next run's memory: a row whose two fingerprints have not
-- moved keeps its verdict without being measured or rendered again.

CREATE TABLE IF NOT EXISTS geometry_compare_elements (
    provider_a   text        NOT NULL,
    provider_b   text        NOT NULL,
    collection   text        NOT NULL,
    key          text        NOT NULL,
    name         text,
    verdict      text        NOT NULL,
    reason       text,
    metrics      jsonb       NOT NULL DEFAULT '{}'::jsonb,
    fp_a         text,
    fp_b         text,
    side_a       jsonb,
    side_b       jsonb,
    picture_a    text,
    picture_b    text,
    run_id       uuid,
    compared_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (provider_a, provider_b, collection, key)
);

CREATE INDEX IF NOT EXISTS geometry_compare_elements_verdict_idx
    ON geometry_compare_elements (provider_a, provider_b, verdict, collection);
