-- 008_audit_schedules.sql — built-in cron scheduler for audit runs.
--
-- An ``audit_schedules`` row defines a recurring audit sweep: every
-- ``cron_expr`` minute the scheduler tick (in src/ada/comms/rest/app.py)
-- claims the row, computes the next firing, and invokes the same
-- dispatcher used by ``POST /admin/audit/runs`` with ``trigger='cron'``.
--
-- The scheduler tick runs inside one API replica's background-task
-- loop. To stay safe across replicas the tick uses a single atomic
-- ``UPDATE … RETURNING`` keyed off ``last_fired_at`` so two replicas
-- racing on the same due row produce at most one fire — losers see
-- their UPDATE affect zero rows and skip.
--
-- Concurrent-fire guard: before dispatching, the tick checks for a
-- still-``running`` audit_runs row with the same (scope, worker_pool)
-- key. If one exists, the schedule's ``last_skipped_reason`` is set
-- and ``last_fired_at`` is bumped anyway (the next slot is the next
-- chance — we don't want to backfire missed slots).

CREATE TABLE IF NOT EXISTS audit_schedules (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- Human label shown in the admin UI. Unique among live rows so
    -- the operator can re-claim a name after archiving an old
    -- schedule with the same purpose.
    name          TEXT NOT NULL,
    -- Standard 5-field cron expression (minute hour dom month dow).
    -- Validated server-side via croniter before insert/update;
    -- malformed expressions surface as 400 from the REST layer.
    cron_expr     TEXT NOT NULL,
    -- Wire-format scope string, same shape audit_runs.scope accepts
    -- ("shared" / "user:..." / "project:..." / "corpus:..."). The
    -- dispatcher resolves it at fire time just like a manual run.
    scope         TEXT NOT NULL,
    -- Optional capability tag — same semantics as
    -- audit_runs.worker_pool. NULL means "any worker pool".
    worker_pool   TEXT,
    enabled       BOOLEAN NOT NULL DEFAULT TRUE,
    -- ``last_fired_at`` doubles as the optimistic-concurrency token
    -- for the scheduler tick: claim-by-UPDATE only succeeds if the
    -- caller's snapshot matches the row in the DB.
    last_fired_at TIMESTAMPTZ,
    -- Pre-computed by the scheduler on insert/update so the tick's
    -- ``WHERE next_fire_at <= NOW()`` is index-friendly.
    next_fire_at  TIMESTAMPTZ,
    -- Free-text reason set when the tick decides not to dispatch
    -- (concurrent-fire guard hit, scope resolution failed, etc.).
    -- Cleared on a successful fire.
    last_skipped_reason TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by    TEXT,
    archived_at   TIMESTAMPTZ
);

-- Name uniqueness is enforced only across live rows so an archived
-- schedule's name is reusable immediately.
CREATE UNIQUE INDEX IF NOT EXISTS audit_schedules_name_live_idx
    ON audit_schedules (name)
    WHERE archived_at IS NULL;

-- The tick query is ``WHERE enabled AND archived_at IS NULL AND
-- next_fire_at <= NOW()`` — a partial index keeps it cheap even as
-- the table grows.
CREATE INDEX IF NOT EXISTS audit_schedules_due_idx
    ON audit_schedules (next_fire_at)
    WHERE enabled AND archived_at IS NULL;
