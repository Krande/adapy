-- 019_worker_packages.sql — per-worker-image package manifest ("pixi list").
--
-- Each worker captures the precise versions of every conda package in its env
-- (occt / pythonocc-core / ada-cpp / ifcopenshell / numpy / …) at startup,
-- keyed by its image tag (the same worker_image_tag already stamped on convert
-- audit rows). A convert audit row → its worker_image_tag → this table gives the
-- exact toolchain that produced an output, so e.g. an ada-cpp version bump or a
-- missing adacpp is attributable.
--
-- One row per image tag (idempotent upsert on restart). ``packages`` is a JSONB
-- array of {name, version, build, channel?} objects.
CREATE TABLE IF NOT EXISTS worker_packages (
    worker_image_tag TEXT PRIMARY KEY,
    packages         JSONB NOT NULL,
    captured_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
