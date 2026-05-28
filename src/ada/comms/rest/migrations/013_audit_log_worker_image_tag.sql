-- 013_audit_log_worker_image_tag.sql — record which worker image
-- processed each conversion.
--
-- The worker pool publishes its ``image_tag`` (built from
-- ``ADA_IMAGE_TAG`` at start-up — e.g. ``sha-4fe483c``) to the
-- registry's NATS-KV record so the admin Workers tab can show
-- "which pod is on which build." That metadata is per-pool, not
-- per-job, so when a regression sweep shows a cell failing the
-- operator can't yet tell whether the failure happened on the
-- image rolled out yesterday or the one before — the cell-level
-- detail screen had no SHA attribution.
--
-- ``audit_log.worker_image_tag`` fills that in: the worker stamps
-- its image tag onto every row it produces. The Audit Runs grid
-- and the per-row drill-in can then surface "processed by
-- sha-XYZ", which makes "when did this regression land?"
-- answerable from the audit panel alone (no log spelunking).

ALTER TABLE audit_log ADD COLUMN worker_image_tag TEXT;
