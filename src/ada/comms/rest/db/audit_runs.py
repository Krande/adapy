"""Audit runs: batches of audit jobs with totals, counters, parity rows and validation reserves."""

from __future__ import annotations

import json

import asyncpg

from ._common import _loads_jsonb


async def abort_audit_run(
    pool: asyncpg.Pool,
    run_id: str,
) -> dict | None:
    """Stop a running audit. Sets the run's status to ``'aborted'``
    and cancels every queued / running child audit_log row in the
    same transaction so the per-cell grid shows where the run was
    when it died (the rows that already finished keep their
    terminal status — we don't rewrite history)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            run = await conn.fetchrow(
                """
                UPDATE audit_runs
                SET status = 'aborted',
                    finished_at = NOW()
                WHERE id = $1
                  AND status = 'running'
                RETURNING id, scope, worker_pool, trigger, started_at,
                          finished_at, status, note, total, ok, failed,
                          skipped, created_by, issue_bot_status,
                          issue_bot_last_error, issue_bot_synced_at
                """,
                run_id,
            )
            if run is None:
                return None
            # Cancel still-queued / still-running children. Counts
            # them as ``skipped`` so the run's ok+failed+skipped
            # matches total once late completions stop landing.
            cancelled_rows = await conn.fetch(
                """
                UPDATE audit_log
                SET status = 'cancelled',
                    error = COALESCE(error, 'audit run aborted')
                WHERE audit_run_id = $1
                  AND status IN ('queued', 'running')
                RETURNING id, job_id
                """,
                run_id,
            )
            n_cancel = len(cancelled_rows)
            if n_cancel > 0:
                await conn.execute(
                    """
                    UPDATE audit_runs
                    SET skipped = skipped + $2
                    WHERE id = $1
                    """,
                    run_id,
                    n_cancel,
                )
    result = _audit_run_row(run)
    # job_ids of the cells we just cancelled — the caller purges their still-queued
    # JetStream messages so the worker never pulls/processes a doomed cell.
    result["cancelled_job_ids"] = [r["job_id"] for r in cancelled_rows if r["job_id"]]
    return result


# ── Audit runs (M1 admin audit panel) ──────────────────────────────


def _audit_run_row(r) -> dict:
    """Project an audit_runs row to its JSON-ready dict shape.

    Includes the M5 issue-bot fields when the underlying row has
    them (it always does post-migration 009, but the helper tolerates
    rows from a SELECT that omits those columns by falling back to
    ``None``)."""

    def _opt(col: str):
        try:
            return r[col]
        except (KeyError, TypeError):
            return None

    issue_bot_synced_at = _opt("issue_bot_synced_at")
    parent_run_id = _opt("parent_run_id")
    av_dispatched = _opt("auto_validate_dispatched_at")
    return {
        "id": str(r["id"]),
        "scope": r["scope"],
        "worker_pool": r["worker_pool"],
        "trigger": r["trigger"],
        "started_at": r["started_at"].isoformat() if r["started_at"] else None,
        "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
        "status": r["status"],
        "note": r["note"],
        "total": r["total"],
        # Parity cells reserved in ``total`` for the auto-validation pass but
        # not yet enqueued (0 once the pass dispatches, or for non-auto runs).
        "validate_total": _opt("validate_total") or 0,
        "ok": r["ok"],
        "failed": r["failed"],
        "skipped": r["skipped"],
        "created_by": r["created_by"],
        "seq": _opt("seq"),
        # Idle time excluded from the run's active duration (gap before a later
        # validation pass). The UI shows (finished_at - started_at) - idle_ms.
        "idle_ms": _opt("idle_ms") or 0,
        # Sum of every cell's own duration_ms — the run's active compute time,
        # independent of wall clock (which parallel workers compress and a
        # single-cell re-run inflates with idle). Recomputes whenever a cell's
        # row changes, so a re-run's new timing is reflected immediately.
        "cells_duration_ms": _opt("cells_duration_ms") or 0,
        "force_rebuild": _opt("force_rebuild") or False,
        "auto_validate": _opt("auto_validate") or False,
        "parent_run_id": str(parent_run_id) if parent_run_id else None,
        "auto_validate_dispatched_at": (av_dispatched.isoformat() if av_dispatched else None),
        "issue_bot_status": _opt("issue_bot_status"),
        "issue_bot_last_error": _opt("issue_bot_last_error"),
        "issue_bot_synced_at": (issue_bot_synced_at.isoformat() if issue_bot_synced_at else None),
    }


async def create_audit_run(
    pool: asyncpg.Pool,
    *,
    scope: str,
    worker_pool: str | None,
    trigger: str = "manual",
    note: str | None = None,
    created_by: str | None = None,
    force_rebuild: bool = False,
    auto_validate: bool = False,
    parent_run_id: str | None = None,
) -> dict:
    """Open a new audit_runs row in ``status='running'``. Returns the
    fresh row (including its server-generated UUID + started_at) so
    the dispatcher can stamp the jobs it enqueues. ``total`` starts
    at 0 — :func:`set_audit_run_total` finalises it once dispatch
    knows how many jobs landed.

    ``force_rebuild`` (M7+) bypasses the dispatcher's cached-cell
    short-circuit so every viable cell actually re-converts.
    Persisted on the row so the admin UI can show "this was a
    force-rebuild" badge.

    ``auto_validate`` asks the finished-run poller to fire a follow-up
    ``validate_only`` parity run once this run finishes. ``parent_run_id``
    links a derived run (the validation child, or a re-dispatched copy)
    back to its source.
    """
    row = await pool.fetchrow(
        """
        INSERT INTO audit_runs
            (scope, worker_pool, trigger, note, created_by, force_rebuild,
             auto_validate, parent_run_id)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        RETURNING *
        """,
        scope,
        worker_pool,
        trigger,
        note,
        created_by,
        force_rebuild,
        auto_validate,
        parent_run_id,
    )
    return _audit_run_row(row)


async def set_audit_run_total(
    pool: asyncpg.Pool,
    run_id: str,
    total: int,
    *,
    validate_total: int = 0,
) -> None:
    """Set the dispatched-job count after enqueue completes. If
    ``total`` is 0 (no jobs to run — empty scope or no viable cells),
    the run is marked ``finished`` immediately so the UI doesn't show
    a perpetually-running row.

    ``validate_total`` reserves that many of the ``total`` cells for the
    auto-validation parity pass, which is dispatched later (once the
    conversion cells have landed) — so the run's total is complete from
    the start. The reservation is consumed by
    :func:`consume_audit_run_validation_reserve`."""
    await pool.execute(
        """
        UPDATE audit_runs
        SET total = $2,
            validate_total = $3,
            status = CASE WHEN $2 = 0 THEN 'finished' ELSE status END,
            finished_at = CASE WHEN $2 = 0 THEN NOW() ELSE finished_at END
        WHERE id = $1
        """,
        run_id,
        total,
        validate_total,
    )


async def extend_audit_run_total(pool: asyncpg.Pool, run_id: str, delta: int) -> None:
    """Grow a finished run's ``total`` by ``delta`` and reopen it to
    ``running`` (clearing ``finished_at``). Used to append cells — an
    auto-validation parity pass — to an existing run instead of spawning a
    separate one: once the appended cells land, the normal counter-bump
    re-finishes the same run. Aborted runs are left aborted. Caller guards
    ``delta > 0``."""
    await pool.execute(
        """
        UPDATE audit_runs
        SET total = total + $2,
            -- Fold the idle gap (since this run last finished) into idle_ms so
            -- the displayed duration stays block-active time, not wall clock.
            -- RHS reads the pre-update finished_at / status.
            idle_ms = idle_ms + CASE
                WHEN status <> 'aborted' AND finished_at IS NOT NULL
                THEN GREATEST(0, (EXTRACT(EPOCH FROM (NOW() - finished_at)) * 1000)::bigint)
                ELSE 0 END,
            status = CASE WHEN status = 'aborted' THEN 'aborted' ELSE 'running' END,
            finished_at = CASE WHEN status = 'aborted' THEN finished_at ELSE NULL END
        WHERE id = $1
        """,
        run_id,
        delta,
    )


async def consume_audit_run_validation_reserve(
    pool: asyncpg.Pool,
    run_id: str,
    actual: int,
) -> None:
    """Swap a run's reserved validation-cell count for the ``actual``
    number of parity cells enumerated at dispatch time.

    ``total`` was set upfront to conversions + reservation; here it is
    corrected by (actual - validate_total) — normally zero, non-zero only
    when the scope's files changed between the two enumerations — and the
    reservation is cleared. If the corrected total is already met (e.g.
    ``actual`` is 0 because the parity enumeration failed or the sources
    vanished), the run finishes here since no counter-bump will arrive to
    do it. Aborted runs only get their bookkeeping corrected."""
    await pool.execute(
        """
        UPDATE audit_runs
        SET total = total - validate_total + $2,
            validate_total = 0,
            finished_at = CASE
                WHEN status = 'aborted' THEN finished_at
                WHEN ok + failed + skipped >= total - validate_total + $2
                    THEN COALESCE(finished_at, NOW())
                ELSE finished_at
            END,
            status = CASE
                WHEN status = 'aborted' THEN 'aborted'
                WHEN ok + failed + skipped >= total - validate_total + $2
                    THEN 'finished'
                ELSE status
            END
        WHERE id = $1
        """,
        run_id,
        actual,
    )


async def delete_audit_run(pool: asyncpg.Pool, run_id: str) -> tuple[bool, list[str]]:
    """Delete an audit run and its audit_log rows. ``audit_parity`` rows cascade
    on the run delete; ``audit_log`` is ON DELETE SET NULL, so we remove those
    rows explicitly rather than orphaning them as run-less audit entries.

    Returns ``(deleted, queued_job_ids)``: ``deleted`` is True if a run row was
    removed; ``queued_job_ids`` are the still-queued cells' job_ids captured BEFORE
    the delete, so the caller can purge their JetStream messages (deleting the rows
    would otherwise make a cancelled cell invisible to the worker's cancel check)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            queued = await conn.fetch(
                "SELECT job_id FROM audit_log WHERE audit_run_id = $1 AND status IN ('queued', 'running')",
                run_id,
            )
            await conn.execute("DELETE FROM audit_log WHERE audit_run_id = $1", run_id)
            res = await conn.execute("DELETE FROM audit_runs WHERE id = $1", run_id)
    # asyncpg returns a command tag like "DELETE 1".
    return res.split()[-1] != "0", [r["job_id"] for r in queued if r["job_id"]]


async def list_audit_runs(
    pool: asyncpg.Pool,
    *,
    limit: int = 50,
    before_started_at: str | None = None,
) -> list[dict]:
    """Reverse-chronological audit_runs scan. Keyset paginated on
    ``started_at`` so new runs landing between requests don't shift
    the page boundary the way an offset would."""
    args: list = []
    where = ""
    if before_started_at:
        args.append(before_started_at)
        where = f" WHERE started_at < ${len(args)}::timestamptz"
    args.append(min(max(limit, 1), 200))
    rows = await pool.fetch(
        f"""
        SELECT id, seq, scope, worker_pool, trigger, started_at, finished_at,
               status, note, total, validate_total, ok, failed, skipped, created_by, idle_ms,
               force_rebuild, auto_validate, parent_run_id, auto_validate_dispatched_at,
               issue_bot_status, issue_bot_last_error, issue_bot_synced_at,
               (SELECT COALESCE(SUM(duration_ms), 0)::bigint FROM audit_log
                WHERE audit_run_id = audit_runs.id) AS cells_duration_ms
        FROM audit_runs
        {where}
        ORDER BY started_at DESC
        LIMIT ${len(args)}
        """,
        *args,
    )
    return [_audit_run_row(r) for r in rows]


async def get_audit_run(pool: asyncpg.Pool, run_id: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT id, seq, scope, worker_pool, trigger, started_at, finished_at,
               status, note, total, validate_total, ok, failed, skipped, created_by, idle_ms,
               force_rebuild, auto_validate, parent_run_id, auto_validate_dispatched_at,
               issue_bot_status, issue_bot_last_error, issue_bot_synced_at,
               (SELECT COALESCE(SUM(duration_ms), 0)::bigint FROM audit_log
                WHERE audit_run_id = audit_runs.id) AS cells_duration_ms
        FROM audit_runs WHERE id = $1
        """,
        run_id,
    )
    return _audit_run_row(row) if row else None


async def list_audit_run_jobs(
    pool: asyncpg.Pool,
    run_id: str,
) -> list[dict]:
    """Every audit_log row tied to one audit_run. Returned in
    insert order (ascending id) so the per-run grid in the admin
    panel can render rows in the deterministic order the
    dispatcher emitted them."""
    rows = await pool.fetch(
        """
        SELECT id, ts, key, target_format, status, error,
               duration_ms, cpu_user_ms, cpu_sys_ms, peak_rss_kb,
               read_bytes, write_bytes, job_id, worker_image_tag, convert_meta
        FROM audit_log
        WHERE audit_run_id = $1
        ORDER BY id ASC
        """,
        run_id,
    )
    return [
        {
            "id": r["id"],
            "ts": r["ts"].isoformat() if r["ts"] else None,
            "key": r["key"],
            "target_format": r["target_format"],
            "status": r["status"],
            "error": r["error"],
            "duration_ms": r["duration_ms"],
            "cpu_user_ms": r["cpu_user_ms"],
            "cpu_sys_ms": r["cpu_sys_ms"],
            "peak_rss_kb": r["peak_rss_kb"],
            "read_bytes": r["read_bytes"],
            "write_bytes": r["write_bytes"],
            "job_id": r["job_id"],
            "worker_image_tag": r["worker_image_tag"],
            "convert_meta": _loads_jsonb(r["convert_meta"]),
        }
        for r in rows
    ]


async def insert_audit_parity(
    pool: asyncpg.Pool,
    *,
    job_id: str | None,
    source_key: str,
    baseline: int,
    counts: dict[str, int],
    consistent: bool,
    mismatches: dict[str, int],
    errors: dict[str, str],
) -> None:
    """Record one cross-format parity result. The owning ``audit_run_id`` is
    resolved from the parity job's audit_log row (the worker doesn't carry it)."""
    await pool.execute(
        """
        INSERT INTO audit_parity
            (audit_run_id, job_id, source_key, baseline, counts, consistent, mismatches, errors)
        VALUES (
            (SELECT audit_run_id FROM audit_log WHERE job_id = $1 ORDER BY id DESC LIMIT 1),
            $1, $2, $3, $4::jsonb, $5, $6::jsonb, $7::jsonb
        )
        """,
        job_id,
        source_key,
        baseline,
        json.dumps(counts),
        consistent,
        json.dumps(mismatches),
        json.dumps(errors),
    )


async def list_audit_run_parity(pool: asyncpg.Pool, run_id: str) -> list[dict]:
    """Every parity result for one audit_run, newest first."""
    rows = await pool.fetch(
        """
        SELECT id, ts, job_id, source_key, baseline, counts, consistent, mismatches, errors
        FROM audit_parity
        WHERE audit_run_id = $1
        ORDER BY id DESC
        """,
        run_id,
    )
    return [
        {
            "id": r["id"],
            "ts": r["ts"].isoformat() if r["ts"] else None,
            "job_id": r["job_id"],
            "source_key": r["source_key"],
            "baseline": r["baseline"],
            "counts": json.loads(r["counts"]) if isinstance(r["counts"], str) else r["counts"],
            "consistent": r["consistent"],
            "mismatches": json.loads(r["mismatches"]) if isinstance(r["mismatches"], str) else r["mismatches"],
            "errors": json.loads(r["errors"]) if isinstance(r["errors"], str) else r["errors"],
        }
        for r in rows
    ]


async def audit_run_exists_for_key(
    pool: asyncpg.Pool,
    scope: str,
    worker_pool: str | None,
) -> bool:
    """Concurrent-fire guard for the scheduler tick (M4). Returns True
    when an ``audit_runs`` row with the same (scope, worker_pool) is
    still ``status='running'``. ``worker_pool=None`` is matched against
    SQL NULL explicitly so the "any pool" tag doesn't collide with a
    schedule that pins a specific pool."""
    if worker_pool is None:
        row = await pool.fetchrow(
            """
            SELECT 1 FROM audit_runs
            WHERE status = 'running'
              AND scope = $1
              AND worker_pool IS NULL
            LIMIT 1
            """,
            scope,
        )
    else:
        row = await pool.fetchrow(
            """
            SELECT 1 FROM audit_runs
            WHERE status = 'running'
              AND scope = $1
              AND worker_pool = $2
            LIMIT 1
            """,
            scope,
            worker_pool,
        )
    return row is not None


async def list_failed_audit_run_jobs(
    pool: asyncpg.Pool,
    run_id: str,
) -> list[dict]:
    """All ``audit_log`` rows in ``run_id`` whose status indicates a
    failure ('error' or 'failed'). Returns the columns the issue-bot
    needs to fingerprint + describe the failure — key, target,
    error message, traceback excerpt. Cached cells (status='done')
    and queued cells that never resolved aren't included; the bot
    only opens issues for real failures."""
    rows = await pool.fetch(
        """
        SELECT id, key, scope_kind, scope_id, target_format,
               status, error, traceback
        FROM audit_log
        WHERE audit_run_id = $1
          AND status IN ('error', 'failed')
        ORDER BY id ASC
        """,
        run_id,
    )
    return [
        {
            "id": r["id"],
            "key": r["key"],
            "scope_kind": r["scope_kind"],
            "scope_id": r["scope_id"],
            "target_format": r["target_format"],
            "status": r["status"],
            "error": r["error"],
            "traceback": r["traceback"],
        }
        for r in rows
    ]
