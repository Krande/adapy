"""Audit log rows: one per conversion / view job, plus per-cell counters and metrics samples."""

from __future__ import annotations

import json

import asyncpg

from ada.config import logger


async def insert_audit(
    pool: asyncpg.Pool,
    *,
    user_sub: str | None,
    scope_kind: str,
    scope_id: str | None,
    action: str,
    key: str | None = None,
    target_format: str | None = None,
    status: str | None = None,
    error: str | None = None,
    duration_ms: int | None = None,
    job_id: str | None = None,
    traceback: str | None = None,
    audit_run_id: str | None = None,
    worker_image_tag: str | None = None,
    read_bytes: int | None = None,
    write_bytes: int | None = None,
    peak_rss_kb: int | None = None,
    client_metrics: dict | None = None,
    failure_key: str | None = None,
) -> None:
    """Insert one audit_log row.

    ``audit_run_id`` links the row back to an admin-triggered
    regression sweep (M1 audit panel). NULL for every user-driven
    convert / upload / download row — only the audit dispatcher
    populates it.

    When the row both carries an ``audit_run_id`` AND lands in a
    terminal status (``done`` / ``ok`` / ``error`` / ``cancelled``
    / ``skipped``), the matching counter on ``audit_runs`` is bumped
    in the same transaction. This is the cached-cell path of the
    dispatcher — derived blobs that already exist get audited as
    ``done`` without enqueueing a job, and the run's ok counter
    needs to advance immediately so the math closes against the
    total.
    """

    counter_col = _AUDIT_RUN_COUNTER_FOR_STATUS.get(status) if audit_run_id is not None and status is not None else None
    if counter_col is None:
        # Hot path — single INSERT, no transaction overhead. Covers
        # every user-driven action and the audit dispatcher's
        # ``status='queued'`` enqueue audit.
        await pool.execute(
            """
            INSERT INTO audit_log
                (user_sub, scope_kind, scope_id, action, key,
                 target_format, status, error, duration_ms, job_id,
                 traceback, audit_run_id, worker_image_tag,
                 read_bytes, write_bytes, peak_rss_kb, client_metrics,
                 failure_key)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                    $14, $15, $16, $17::jsonb, $18)
            """,
            user_sub,
            scope_kind,
            scope_id,
            action,
            key,
            target_format,
            status,
            error,
            duration_ms,
            job_id,
            traceback,
            audit_run_id,
            worker_image_tag,
            read_bytes,
            write_bytes,
            peak_rss_kb,
            json.dumps(client_metrics) if client_metrics is not None else None,
            failure_key,
        )
        return

    # Audit-dispatcher cached-cell path: insert + counter bump in one
    # transaction so a crash between the two can't leave a row counted
    # by the audit grid but not by the run total.
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO audit_log
                    (user_sub, scope_kind, scope_id, action, key,
                     target_format, status, error, duration_ms, job_id,
                     traceback, audit_run_id, worker_image_tag,
                     read_bytes, write_bytes, peak_rss_kb, client_metrics,
                     failure_key)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                        $14, $15, $16, $17::jsonb, $18)
                """,
                user_sub,
                scope_kind,
                scope_id,
                action,
                key,
                target_format,
                status,
                error,
                duration_ms,
                job_id,
                traceback,
                audit_run_id,
                worker_image_tag,
                read_bytes,
                write_bytes,
                peak_rss_kb,
                json.dumps(client_metrics) if client_metrics is not None else None,
                failure_key,
            )
            await _bump_audit_run_counter(conn, audit_run_id, status)


async def cancel_audit_by_job(
    pool: asyncpg.Pool,
    *,
    job_id: str,
    user_sub: str,
) -> bool:
    """Mark a queued or running job as cancelled. Caller must own it.

    Returns ``True`` if a row was updated, ``False`` otherwise (row
    missing, status already terminal, or owned by someone else).
    Filtering on ``status`` lets the SQL itself enforce the "only
    cancel queued/running" rule so a race between two cancel clicks
    can't mark a done job cancelled retroactively.
    """
    result = await pool.execute(
        """
        UPDATE audit_log
        SET status = 'cancelled',
            error = COALESCE(error, 'cancelled by user')
        WHERE job_id = $1
          AND user_sub = $2
          AND status IN ('queued', 'running')
        """,
        job_id,
        user_sub,
    )
    return result.endswith(" 1")


async def admin_cancel_audit_by_job(
    pool: asyncpg.Pool,
    *,
    job_id: str,
    reason: str = "cancelled by an administrator",
) -> bool:
    """Cancel a job REGARDLESS of who started it. Admin-gated at the route.

    The same UPDATE as :func:`cancel_audit_by_job` without the ownership
    filter, and deliberately a separate function rather than an optional
    ``user_sub=None``: a default that silently disables the owner check is one
    forgotten keyword away from letting any user cancel anybody's work. Two
    names, two call sites, no way to reach this one by omission.

    The ``status IN ('queued','running')`` filter is kept for the same reason
    it exists there -- a terminal row must not be rewritten retroactively, and
    that rule does not relax for an admin.
    """
    result = await pool.execute(
        """
        UPDATE audit_log
        SET status = 'cancelled',
            error = COALESCE(error, $2)
        WHERE job_id = $1
          AND status IN ('queued', 'running')
        """,
        job_id,
        reason,
    )
    return result.endswith(" 1")


async def audit_is_cancelled(pool: asyncpg.Pool, job_id: str) -> bool:
    """True once the job's audit row has been flipped to ``cancelled`` (the cancel
    endpoint's source of truth — the KV status is overwritten by worker progress
    writes, so it can't be trusted). Polled by the conversion watchdog so an
    actively-running conversion is actually reaped instead of running to completion."""
    row = await pool.fetchrow(
        "SELECT 1 FROM audit_log WHERE job_id = $1 AND status = 'cancelled' LIMIT 1",
        job_id,
    )
    return row is not None


async def get_audit_owner_by_job(pool: asyncpg.Pool, job_id: str) -> dict | None:
    """Return ``{user_sub, scope_kind, scope_id, audit_run_id, status}``
    for the audit row tied to ``job_id``, or ``None`` if absent.

    Used by the browser-driven (WASM) audit-update endpoint to verify
    the caller owns the row before patching it — the worker's
    ``update_audit_by_job`` has no such guard because only the worker
    that owns the NATS job can reach it, whereas the local endpoint is
    callable by any authenticated browser.
    """
    row = await pool.fetchrow(
        """
        SELECT user_sub, scope_kind, scope_id, audit_run_id, status
        FROM audit_log
        WHERE job_id = $1
        ORDER BY id DESC
        LIMIT 1
        """,
        job_id,
    )
    return dict(row) if row is not None else None


async def mark_audit_running(
    pool: asyncpg.Pool,
    *,
    job_id: str,
    worker_image_tag: str | None = None,
) -> None:
    """Flip the audit_log row for a job into ``status='running'`` and stamp
    ``started_at`` so the admin's "current cell" query has a fresh row.

    Without this hop the row goes straight queued → done and the audit toast's
    ORDER BY can't tell which queued cell is actually being worked on — so the
    same one stays on screen for the whole sweep.

    THIS USED TO OVERWRITE ``ts``, and that is why queue wait could not be
    measured after the fact: ``ts`` was the enqueue time while a row was queued
    and the START time from the moment it was picked up, so the interval between
    the two — the wait — was destroyed by the very hop that ended it. It also
    made ``ts`` mean two different things depending on a column next to it,
    which is a trap for anything reading the table.

    ``ts`` is now the enqueue time for the row's whole life, and ``started_at``
    is the pickup. Wait is the difference. Best-effort: a DB hiccup must never
    break job processing.
    """
    if pool is None:
        return
    try:
        await pool.execute(
            """
            UPDATE audit_log
            SET status = 'running',
                started_at = NOW(),
                worker_image_tag = COALESCE($2, worker_image_tag)
            WHERE job_id = $1
              AND status = 'queued'
            """,
            job_id,
            worker_image_tag,
        )
    except Exception:
        logger.exception("worker: mark_audit_running failed for job %s", job_id)


async def update_audit_by_job(
    pool: asyncpg.Pool,
    *,
    job_id: str,
    status: str,
    error: str | None = None,
    duration_ms: int | None = None,
    traceback: str | None = None,
    cpu_user_ms: int | None = None,
    cpu_sys_ms: int | None = None,
    peak_rss_kb: int | None = None,
    read_bytes: int | None = None,
    write_bytes: int | None = None,
    profile_key: str | None = None,
    log_key: str | None = None,
    worker_image_tag: str | None = None,
    convert_meta: dict | None = None,
    failure_key: str | None = None,
) -> None:
    """Patch the audit row tied to a queue job with its final outcome.

    No-op when the row is missing (job predates the migration, or the
    enqueue-time audit insert failed). COALESCE preserves any existing
    column when the caller passes None.

    When the row carries an ``audit_run_id`` (M1 audit panel), the
    matching counter on ``audit_runs`` (``ok`` / ``failed`` /
    ``skipped``) is bumped in the same transaction. The run flips to
    ``status='finished'`` when ``ok + failed + skipped`` reaches
    ``total``. Regular user-driven jobs (audit_run_id IS NULL) take
    the original single-table path with no overhead.
    """
    # Single transaction so the per-row write + the run-counter bump
    # never split — a worker restart between the two would otherwise
    # leak a job from the counters and the run would never finish.
    async with pool.acquire() as conn:
        async with conn.transaction():
            updated = await conn.fetchrow(
                """
                UPDATE audit_log
                SET status = $2,
                    error = COALESCE($3, error),
                    duration_ms = COALESCE($4, duration_ms),
                    traceback = COALESCE($5, traceback),
                    cpu_user_ms = COALESCE($6, cpu_user_ms),
                    cpu_sys_ms = COALESCE($7, cpu_sys_ms),
                    peak_rss_kb = COALESCE($8, peak_rss_kb),
                    read_bytes = COALESCE($9, read_bytes),
                    write_bytes = COALESCE($10, write_bytes),
                    profile_key = COALESCE($11, profile_key),
                    worker_image_tag = COALESCE($12, worker_image_tag),
                    convert_meta = COALESCE($13, convert_meta),
                    log_key = COALESCE($14, log_key),
                    failure_key = COALESCE($15, failure_key)
                WHERE job_id = $1
                RETURNING audit_run_id
                """,
                job_id,
                status,
                error,
                duration_ms,
                traceback,
                cpu_user_ms,
                cpu_sys_ms,
                peak_rss_kb,
                read_bytes,
                write_bytes,
                profile_key,
                worker_image_tag,
                json.dumps(convert_meta) if convert_meta is not None else None,
                log_key,
                failure_key,
            )
            if updated is None or updated["audit_run_id"] is None:
                return
            await _bump_audit_run_counter(conn, updated["audit_run_id"], status)


# Map terminal job-status values to the audit_runs counter column they
# bump. Statuses outside this set (``running`` / ``queued``) don't
# advance any counter — only terminal transitions do.
_AUDIT_RUN_COUNTER_FOR_STATUS = {
    "done": "ok",
    "ok": "ok",
    "error": "failed",
    "failed": "failed",
    "cancelled": "skipped",
    "skipped": "skipped",
}


async def _bump_audit_run_counter(
    conn: asyncpg.Connection,
    run_id,
    terminal_status: str,
) -> None:
    """Increment one of the run's terminal counters and finish the
    run when all enqueued jobs have landed. Connection-bound (not
    pool-bound) so the caller can run this inside the same
    transaction as the audit_log UPDATE.

    ``status='aborted'`` (an admin pressed Cancel) is preserved —
    late worker completions arriving after the abort still bump
    counters for diagnostics, but the run never auto-flips back to
    ``finished``.
    """
    column = _AUDIT_RUN_COUNTER_FOR_STATUS.get(terminal_status)
    if column is None:
        # Transient state (``running``); nothing to bump yet.
        return
    # The SET clause uses dynamic column interpolation but ``column``
    # is constrained to a closed allowlist above, so f-string here is
    # safe — no caller-supplied SQL surface.
    await conn.execute(
        f"""
        UPDATE audit_runs
        SET {column} = {column} + 1,
            finished_at = CASE
                WHEN status = 'aborted' THEN finished_at
                WHEN ok + failed + skipped + 1 >= total
                  THEN COALESCE(finished_at, NOW())
                ELSE finished_at
            END,
            status = CASE
                WHEN status = 'aborted' THEN 'aborted'
                WHEN ok + failed + skipped + 1 >= total
                  THEN 'finished'
                ELSE status
            END
        WHERE id = $1
        """,
        run_id,
    )


async def reset_audit_cell_for_rerun(
    pool: asyncpg.Pool,
    run_id: str,
    key: str,
    target_format: str,
    new_job_id: str,
) -> bool:
    """Re-point a single audit cell at a fresh conversion job so it can be
    re-run in place, keeping the run's counters and total correct.

    One row exists per cell in a run (the dispatcher inserts it ``queued`` and
    the worker updates it by ``job_id``). To re-run one cell we: undo the
    counter its prior terminal status bumped (so ``failed`` drops when a
    previously-failed cell is retried), clear its result fields, attach the new
    ``job_id`` and set it back to ``queued``, and reopen the run. The worker's
    normal completion path (``update_audit_by_job`` → the counter bump)
    then re-increments and re-finishes the run exactly as for a first run.

    Returns False if the cell isn't part of the run (nothing to re-run)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                """
                SELECT id, status FROM audit_log
                WHERE audit_run_id = $1 AND key = $2 AND target_format = $3
                ORDER BY id DESC
                LIMIT 1
                """,
                run_id,
                key,
                target_format,
            )
            if row is None:
                return False
            prior_col = _AUDIT_RUN_COUNTER_FOR_STATUS.get(row["status"] or "")
            await conn.execute(
                """
                UPDATE audit_log
                SET job_id = $2, status = 'queued', error = NULL, traceback = NULL,
                    duration_ms = NULL, cpu_user_ms = NULL, cpu_sys_ms = NULL,
                    peak_rss_kb = NULL, read_bytes = NULL, write_bytes = NULL,
                    profile_key = NULL, log_key = NULL, ts = NOW()
                WHERE id = $1
                """,
                row["id"],
                new_job_id,
            )
            # Undo the prior terminal counter (if any) and reopen the run. The
            # column name comes from a closed allowlist so the f-string is safe.
            dec = f"{prior_col} = GREATEST({prior_col} - 1, 0)," if prior_col else ""
            await conn.execute(
                f"""
                UPDATE audit_runs
                SET {dec}
                    -- Fold the idle gap since this run last finished into idle_ms
                    -- (same as extend_audit_run_total). Then when the re-run cell
                    -- lands and re-finishes the run, the wall-clock duration grows
                    -- ONLY by the re-run's own delta, not the days-long gap since
                    -- the original run. RHS reads the pre-update finished_at.
                    idle_ms = idle_ms + CASE
                        WHEN status <> 'aborted' AND finished_at IS NOT NULL
                        THEN GREATEST(0, (EXTRACT(EPOCH FROM (NOW() - finished_at)) * 1000)::bigint)
                        ELSE 0 END,
                    status = CASE WHEN status = 'aborted' THEN 'aborted' ELSE 'running' END,
                    finished_at = CASE WHEN status = 'aborted' THEN finished_at ELSE NULL END
                WHERE id = $1
                """,
                run_id,
            )
            return True


async def active_audit_summary(pool: asyncpg.Pool) -> dict:
    """Aggregate state of currently-``running`` audit runs.

    Used by the bottom-right viewer toast to show an ambient
    "N audit runs · M cells pending" badge that links into the
    admin panel. Plus the most recently-touched in-flight cell
    (status ``running`` preferred, falls back to ``queued``) so
    the operator sees what's actually converting right now —
    handy on a force-rebuild measurement run where every cell
    actually executes.

    Returns:
        {"running_runs": int, "pending_cells": int,
         "current_cell": {key, target_format, status,
                          started_at, elapsed_ms} | None}
    """
    counts_row = await pool.fetchrow(
        """
        SELECT
            COUNT(*) AS running_runs,
            COALESCE(SUM(GREATEST(total - ok - failed - skipped, 0)), 0)
                AS pending_cells
        FROM audit_runs
        WHERE status = 'running'
        """
    )
    # Only surface rows where the worker has actually picked the
    # cell up — i.e. ``audit_log.status = 'running'`` (set by
    # ``mark_audit_running`` at the start of ``_process_one``).
    # Queued rows don't reflect what's converting right now; the
    # dispatcher inserts them all with the same ts at the start of
    # the sweep, so picking the "most recent queued" gives a row
    # that doesn't change as the sweep advances. Caller sees no
    # current_cell between cells (a few hundred ms) — that's the
    # honest answer, and the toast hides the line there.
    #
    # Ordered by ``started_at``, which is what "most recently picked up" means
    # now that ``ts`` stays at enqueue. COALESCE keeps a mid-rollout row written
    # by an older worker (started_at NULL, ts stamped at pickup) in the right
    # place instead of sorting it to the bottom for the length of the sweep.
    current_row = await pool.fetchrow(
        """
        SELECT al.key, al.target_format, al.status,
               COALESCE(al.started_at, al.ts) AS ts
        FROM audit_log al
        JOIN audit_runs ar ON ar.id = al.audit_run_id
        WHERE ar.status = 'running'
          AND al.status = 'running'
        ORDER BY COALESCE(al.started_at, al.ts) DESC
        LIMIT 1
        """
    )
    current_cell = None
    if current_row is not None:
        ts = current_row["ts"]
        elapsed_ms = None
        if ts is not None:
            from datetime import datetime, timezone

            elapsed_ms = int((datetime.now(timezone.utc) - ts).total_seconds() * 1000)
        current_cell = {
            "key": current_row["key"],
            "target_format": current_row["target_format"],
            "status": current_row["status"],
            "started_at": ts.isoformat() if ts else None,
            "elapsed_ms": elapsed_ms,
        }
    return {
        "running_runs": int(counts_row["running_runs"] or 0),
        "pending_cells": int(counts_row["pending_cells"] or 0),
        "current_cell": current_cell,
    }


async def append_metrics_sample_by_job(
    pool: asyncpg.Pool,
    *,
    job_id: str,
    sample: dict,
) -> None:
    """Append one heartbeat sample to ``audit_log.metrics_samples``.

    The column is JSONB initialised to NULL when the row is created;
    we coalesce to ``[]`` before appending so the first heartbeat
    on a fresh row still produces a valid array. Best-effort —
    callers swallow exceptions because losing one heartbeat shouldn't
    fail the conversion.
    """
    await pool.execute(
        """
        UPDATE audit_log
        SET metrics_samples = COALESCE(metrics_samples, '[]'::jsonb) || $2::jsonb
        WHERE job_id = $1
        """,
        job_id,
        json.dumps(sample),
    )
