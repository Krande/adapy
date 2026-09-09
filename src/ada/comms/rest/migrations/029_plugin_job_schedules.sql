-- 029_plugin_job_schedules.sql — cron for plugin jobs.
--
-- WHAT THIS IS FOR. A plugin job that should run on a schedule had nowhere to be
-- scheduled from. The alternative was a cron on the machine the worker runs on,
-- POSTing to this API — which means that machine holds a credential able to
-- enqueue, the schedule is invisible to the admin panel, and changing when it
-- runs means logging into the box. For a plugin driving a licensed workstation
-- that is the wrong place for all three.
--
-- A row here defines a recurring plugin job: every tick the scheduler claims the
-- due row and enqueues it through exactly the path ``POST /plugins/{id}/jobs``
-- uses. The worker cannot tell a scheduled firing from a user-initiated one, and
-- that is the point — capability routing, audit rows, cancellation and the
-- cached-blob short circuit all behave identically because none of them are
-- re-implemented here.
--
-- SEPARATE FROM ``audit_schedules`` RATHER THAN GENERALISING IT. The two tables
-- carry different payloads: an audit run names a worker pool and a sweep, a
-- plugin job names a plugin and an arbitrary options document. Folding them
-- together would mean a nullable column per difference and a `kind` discriminator
-- on a table that is already firing in production — a data migration on live
-- scheduling, to save a table. The tick logic IS shared in spirit and deliberately
-- mirrored in shape, so the two read the same way side by side.
--
-- WHY THERE IS NO ``allow_cache``. Core hashes a plugin job's options into its
-- source key so identical requests cache-hit. A schedule sends byte-identical
-- options on every firing, so without intervention the second run and every one
-- after it would return the FIRST run's summary: hourly green ticks, the worker
-- never touched, and a caller believing it has fresh data. The tick therefore
-- always varies the options it dispatches (see ``_plugin_schedule_fire``), and
-- the absence of a knob here is the decision — a scheduled run that legitimately
-- answers "same as last time" is indistinguishable from one that did not happen.

CREATE TABLE IF NOT EXISTS plugin_job_schedules (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Human label shown in the admin UI, and echoed into the audit row so a
    -- firing can be traced back to the schedule that caused it.
    name          TEXT NOT NULL,
    -- Standard 5-field cron expression. Validated with croniter before
    -- insert/update, so a malformed value is a 400 from the REST layer rather
    -- than a schedule that silently never fires.
    cron_expr     TEXT NOT NULL,
    -- Wire-format scope string ("shared", "project:<slug-or-uuid>"), resolved at
    -- fire time rather than stored resolved: a project renamed between creation
    -- and firing should still work, and the resolution is the same code the
    -- route uses.
    scope         TEXT NOT NULL,
    -- Which plugin's job to run. Not an FK: plugins are advertised by live
    -- workers, so the set of valid values is not a table and a schedule may
    -- legitimately outlive the worker that served it.
    plugin_id     TEXT NOT NULL,
    -- The options document handed to the plugin, verbatim. The plugin owns its
    -- shape; core neither validates nor interprets it beyond hashing it.
    options       JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Optional capability override. NULL routes to whatever the plugin's live
    -- spec advertises, which is what a single-pool deployment wants.
    capability    TEXT,
    enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    last_fired_at TIMESTAMPTZ,
    next_fire_at  TIMESTAMPTZ,
    -- Why the last due slot did NOT produce a job. Set when the tick skips --
    -- an unresolvable scope, a previous run still in flight -- and cleared on a
    -- successful claim. Without it a skipped schedule is indistinguishable from
    -- one that fired and whose job failed somewhere else entirely.
    last_skipped_reason TEXT,
    -- The job id of the most recent firing, so the admin panel can link a
    -- schedule straight to what it produced.
    last_job_id   TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by    TEXT,
    -- Soft delete, matching audit_schedules: the audit rows a schedule produced
    -- outlive it, and the name becomes re-usable once archived.
    archived_at   TIMESTAMPTZ
);

-- Name uniqueness only among live rows, so an archived schedule's name can be
-- re-claimed for a new one with the same purpose.
CREATE UNIQUE INDEX IF NOT EXISTS plugin_job_schedules_live_name
    ON plugin_job_schedules (name) WHERE archived_at IS NULL;

-- The tick's claim query: the next enabled, unarchived row that is due.
CREATE INDEX IF NOT EXISTS plugin_job_schedules_due
    ON plugin_job_schedules (next_fire_at)
    WHERE enabled AND archived_at IS NULL;
