"""Issue-bot claims and markers for audit runs and single failed conversions."""

from __future__ import annotations

import asyncpg

from .audit_runs import _audit_run_row

# ── Audit issue-bot (M5 admin audit panel) ─────────────────────────


async def claim_audit_run_for_issue_bot(
    pool: asyncpg.Pool,
):
    """Atomically claim the oldest finished audit_run that hasn't
    been issue-synced yet. Sets ``issue_bot_status='syncing'`` so
    other replicas / retries skip it.

    Returns the claimed row (dict shaped like the public audit_run
    projection) or ``None`` if nothing is pending. The caller is
    expected to call :func:`mark_audit_run_issue_bot` with a
    terminal state once the sync completes (or fails).
    """
    row = await pool.fetchrow(
        """
        UPDATE audit_runs
        SET issue_bot_status = 'syncing'
        WHERE id = (
            SELECT id FROM audit_runs
            WHERE status = 'finished'
              AND issue_bot_status IS NULL
            ORDER BY finished_at ASC NULLS LAST
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, scope, worker_pool, trigger, started_at, finished_at,
                  status, note, total, ok, failed, skipped, created_by,
                  force_rebuild,
                  issue_bot_status, issue_bot_last_error, issue_bot_synced_at
        """,
    )
    return _audit_run_row(row) if row else None


async def claim_audit_run_for_auto_validate(pool: asyncpg.Pool):
    """Atomically claim the oldest ``auto_validate`` run that is ready for its
    validation pass. Two shapes qualify:

    * a run whose parity cells were reserved upfront (``validate_total > 0``)
      and whose conversion cells have all landed — it is still ``running``
      because the reserved cells keep the counter-bump finish check from
      tripping;
    * a pre-reservation run (``validate_total = 0``) that already finished —
      its validation extends the total, the legacy behaviour.

    Stamps ``auto_validate_dispatched_at`` in the same statement so a
    concurrent tick / replica can't double-fire. Returns the claimed run row
    or ``None``."""
    row = await pool.fetchrow(
        """
        UPDATE audit_runs
        SET auto_validate_dispatched_at = NOW()
        WHERE id = (
            SELECT id FROM audit_runs
            WHERE auto_validate = TRUE
              AND auto_validate_dispatched_at IS NULL
              AND (
                    (validate_total > 0 AND status = 'running'
                        AND ok + failed + skipped >= total - validate_total)
                 OR (validate_total = 0 AND status = 'finished')
              )
            ORDER BY started_at ASC NULLS LAST
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, scope, worker_pool, trigger, started_at, finished_at,
                  status, note, total, validate_total, ok, failed, skipped,
                  created_by, force_rebuild, auto_validate, parent_run_id,
                  issue_bot_status, issue_bot_last_error, issue_bot_synced_at
        """,
    )
    return _audit_run_row(row) if row else None


async def claim_run_for_validation(pool: asyncpg.Pool, run_id: str):
    """Atomically mark one *specific* finished run as having its validation
    pass dispatched (the manual 'Validate' button). Returns the run if newly
    claimed, or ``None`` when it's already been validated, isn't finished, or
    doesn't exist — so a validation is appended at most once per run, whether
    via the auto-validate toggle or the button."""
    row = await pool.fetchrow(
        """
        UPDATE audit_runs
        SET auto_validate_dispatched_at = NOW()
        WHERE id = $1
          AND status = 'finished'
          AND auto_validate_dispatched_at IS NULL
        RETURNING id, scope, worker_pool, trigger, started_at, finished_at,
                  status, note, total, ok, failed, skipped, created_by,
                  force_rebuild, auto_validate, parent_run_id, auto_validate_dispatched_at,
                  issue_bot_status, issue_bot_last_error, issue_bot_synced_at
        """,
        run_id,
    )
    return _audit_run_row(row) if row else None


async def audit_log_history_for_cell(
    pool: asyncpg.Pool,
    key: str,
    target_format: str,
    *,
    limit: int = 50,
) -> list[dict]:
    """Historic ``audit_log`` results for one ``(source key, target_format)``
    cell across every run — newest first. Powers the per-cell 'show history'
    table so an operator can spot run-to-run regressions for one conversion.

    Only rows tied to an audit run (``audit_run_id IS NOT NULL``) are returned:
    history is a cross-run comparison, so on-demand / viewer-triggered
    re-conversions (e.g. a GLB re-tessellation on file open, ``audit_run_id``
    NULL) are excluded — otherwise a heavily-viewed source floods its glb cell
    with standalone rows that push the actual per-run results past ``limit``."""
    rows = await pool.fetch(
        """
        SELECT id, ts, status, error, duration_ms, peak_rss_kb,
               worker_image_tag, audit_run_id
        FROM audit_log
        WHERE key = $1 AND target_format = $2 AND audit_run_id IS NOT NULL
        ORDER BY id DESC
        LIMIT $3
        """,
        key,
        target_format,
        min(max(limit, 1), 500),
    )
    return [
        {
            "id": r["id"],
            "ts": r["ts"].isoformat() if r["ts"] else None,
            "status": r["status"],
            "error": r["error"],
            "duration_ms": r["duration_ms"],
            "peak_rss_kb": r["peak_rss_kb"],
            "worker_image_tag": r["worker_image_tag"],
            "audit_run_id": str(r["audit_run_id"]) if r["audit_run_id"] else None,
        }
        for r in rows
    ]


async def mark_audit_run_issue_bot(
    pool: asyncpg.Pool,
    run_id: str,
    *,
    status: str,
    error: str | None = None,
) -> None:
    """Stamp a terminal issue-bot status on the run. ``status`` is
    'done' (issues synced), 'skipped' (no failures to sync / bot
    disabled), or 'failed' (raised mid-sync — ``error`` carries the
    summary). All three set ``issue_bot_synced_at`` to NOW() so the
    UI can show how recently the bot ran."""
    await pool.execute(
        """
        UPDATE audit_runs
        SET issue_bot_status = $2,
            issue_bot_last_error = $3,
            issue_bot_synced_at = NOW()
        WHERE id = $1
        """,
        run_id,
        status,
        error,
    )


async def reset_audit_run_issue_bot(
    pool: asyncpg.Pool,
    run_id: str,
) -> bool:
    """Clear the issue-bot status so the next tick picks the run up
    again. Used by the admin "retry sync" button. Returns True on a
    real reset, False when the run wasn't found or wasn't finished
    (in which case retrying makes no sense)."""
    result = await pool.execute(
        """
        UPDATE audit_runs
        SET issue_bot_status = NULL,
            issue_bot_last_error = NULL,
            issue_bot_synced_at = NULL
        WHERE id = $1 AND status = 'finished'
        """,
        run_id,
    )
    return result.endswith(" 1")


# ── Single-conversion issue-bot (M5b) ──────────────────────────────


async def claim_failed_conversion_for_issue_bot(
    pool: asyncpg.Pool,
) -> dict | None:
    """Atomically claim the oldest failed user-driven conversion
    that hasn't been synced yet.

    Restricted to ``audit_run_id IS NULL`` rows so audit-sweep
    failures keep going through the parent run's bot pass — that
    path batches all of a run's failures into one sync, which is
    much friendlier on the forge API than firing N times.
    """
    row = await pool.fetchrow(
        """
        UPDATE audit_log
        SET issue_bot_status = 'syncing'
        WHERE id = (
            SELECT id FROM audit_log
            WHERE status IN ('error', 'failed')
              AND audit_run_id IS NULL
              AND issue_bot_status IS NULL
            ORDER BY id ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING id, ts, user_sub, scope_kind, scope_id, action,
                  key, target_format, status, error, traceback
        """,
    )
    if row is None:
        return None
    return {
        "id": row["id"],
        "ts": row["ts"].isoformat() if row["ts"] else None,
        "user_sub": row["user_sub"],
        "scope_kind": row["scope_kind"],
        "scope_id": row["scope_id"],
        "action": row["action"],
        "key": row["key"],
        "target_format": row["target_format"],
        "status": row["status"],
        "error": row["error"],
        "traceback": row["traceback"],
    }


async def mark_audit_log_issue_bot(
    pool: asyncpg.Pool,
    audit_id: int,
    *,
    status: str,
    error: str | None = None,
) -> None:
    """Stamp a terminal issue-bot status on one audit_log row. Mirrors
    :func:`mark_audit_run_issue_bot` for the per-row case."""
    await pool.execute(
        """
        UPDATE audit_log
        SET issue_bot_status = $2,
            issue_bot_last_error = $3,
            issue_bot_synced_at = NOW()
        WHERE id = $1
        """,
        audit_id,
        status,
        error,
    )


async def reset_audit_log_issue_bot(
    pool: asyncpg.Pool,
    audit_id: int,
) -> bool:
    """Clear the bot status on one audit_log row so the next tick
    re-syncs it. Used by the admin "retry sync" button. Returns
    True on a real reset, False when the row wasn't found or
    isn't in a terminal failure state (queued/running rows don't
    have anything to sync)."""
    result = await pool.execute(
        """
        UPDATE audit_log
        SET issue_bot_status = NULL,
            issue_bot_last_error = NULL,
            issue_bot_synced_at = NULL
        WHERE id = $1 AND status IN ('error', 'failed')
        """,
        audit_id,
    )
    return result.endswith(" 1")
