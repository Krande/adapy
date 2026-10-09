"""Issue-bot claims and markers for audit runs and single failed conversions."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import AsyncIterator

import asyncpg

from ._common import _loads_jsonb
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


# ── Issue recheck (migration 032) ──────────────────────────────────


async def backfill_audit_log_issue_fps(pool: asyncpg.Pool, *, limit: int = 500) -> int:
    """Fingerprint up to ``limit`` failed audit_log rows that have no ``issue_fp`` yet, and
    return how many were stamped. The fingerprint is a pure function of the row, so this
    covers rows that failed before migration 032 as well as new ones -- the poller calls it
    every tick, and a recheck calls it once before reading the rows for a fingerprint."""
    from ..audit_issue import fingerprint_job

    rows = await pool.fetch(
        """
        SELECT id, key, target_format, error, traceback
        FROM audit_log
        WHERE status IN ('error', 'failed') AND issue_fp IS NULL
        ORDER BY id ASC
        LIMIT $1
        """,
        limit,
    )
    if not rows:
        return 0
    await pool.executemany(
        "UPDATE audit_log SET issue_fp = $2 WHERE id = $1",
        [(r["id"], fingerprint_job(dict(r))) for r in rows],
    )
    return len(rows)


async def list_issue_fp_rows(pool: asyncpg.Pool, fp: str, *, limit: int = 1000) -> list[dict]:
    """The failing rows behind fingerprint ``fp``, newest first, with what a recheck needs to
    re-run each: scope, key, target, the preserved ``failure_key`` (the failure-corpus copy,
    when capture took one) and the worker pool of the run it failed in (NULL for a user
    conversion). ``audit_issue.recheck_cells`` turns these into the cells to re-run."""
    rows = await pool.fetch(
        """
        SELECT al.scope_kind, al.scope_id, al.key, al.target_format, al.failure_key,
               ar.worker_pool
        FROM audit_log al
        LEFT JOIN audit_runs ar ON ar.id = al.audit_run_id
        WHERE al.issue_fp = $1
        ORDER BY al.id DESC
        LIMIT $2
        """,
        fp,
        limit,
    )
    return [dict(r) for r in rows]


async def count_issue_fp_cells(pool: asyncpg.Pool, fps: list[str]) -> dict[str, int]:
    """Distinct failing cells per fingerprint, for the admin issue list."""
    if not fps:
        return {}
    rows = await pool.fetch(
        """
        SELECT issue_fp, COUNT(DISTINCT (scope_kind, scope_id, key, target_format)) AS n
        FROM audit_log
        WHERE issue_fp = ANY($1::text[])
        GROUP BY issue_fp
        """,
        fps,
    )
    return {r["issue_fp"]: int(r["n"]) for r in rows}


def _recheck_row(r) -> dict:
    return {
        "id": r["id"],
        "fp": r["fp"],
        "run_id": str(r["run_id"]),
        "cells": [tuple(c) for c in _loads_jsonb(r["cells"]) or []],
        "created_by": r["created_by"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "verdict": r["verdict"],
        "verdict_detail": r["verdict_detail"],
        "verdict_at": r["verdict_at"].isoformat() if r["verdict_at"] else None,
    }


async def create_issue_recheck(
    pool: asyncpg.Pool,
    *,
    fp: str,
    run_id: str,
    cells: list[tuple[str, str]],
    created_by: str | None,
) -> dict:
    """Record that ``run_id`` re-runs ``cells`` (``(key, target)`` in the run's scope) to
    recheck fingerprint ``fp``."""
    row = await pool.fetchrow(
        """
        INSERT INTO audit_issue_rechecks (fp, run_id, cells, created_by)
        VALUES ($1, $2, $3::jsonb, $4)
        RETURNING *
        """,
        fp,
        run_id,
        json.dumps([list(c) for c in cells]),
        created_by,
    )
    return _recheck_row(row)


async def list_rechecks_for_run(pool: asyncpg.Pool, run_id: str) -> list[dict]:
    rows = await pool.fetch(
        "SELECT * FROM audit_issue_rechecks WHERE run_id = $1 ORDER BY id ASC",
        run_id,
    )
    return [_recheck_row(r) for r in rows]


async def latest_rechecks(pool: asyncpg.Pool, fps: list[str]) -> dict[str, dict]:
    """The most recent recheck per fingerprint, for the admin issue list."""
    if not fps:
        return {}
    rows = await pool.fetch(
        """
        SELECT DISTINCT ON (fp) *
        FROM audit_issue_rechecks
        WHERE fp = ANY($1::text[])
        ORDER BY fp, created_at DESC, id DESC
        """,
        fps,
    )
    return {r["fp"]: _recheck_row(r) for r in rows}


async def pending_recheck_fps(pool: asyncpg.Pool, fps: list[str]) -> set[str]:
    """Fingerprints with a recheck whose run is still going -- a second recheck of one of
    these would only race the first."""
    if not fps:
        return set()
    rows = await pool.fetch(
        """
        SELECT DISTINCT rc.fp
        FROM audit_issue_rechecks rc
        JOIN audit_runs ar ON ar.id = rc.run_id
        WHERE rc.fp = ANY($1::text[]) AND rc.verdict IS NULL AND ar.status = 'running'
        """,
        fps,
    )
    return {r["fp"] for r in rows}


async def set_issue_recheck_verdict(
    pool: asyncpg.Pool,
    recheck_id: int,
    *,
    verdict: str,
    detail: str | None = None,
) -> None:
    await pool.execute(
        """
        UPDATE audit_issue_rechecks
        SET verdict = $2, verdict_detail = $3, verdict_at = NOW()
        WHERE id = $1
        """,
        recheck_id,
        verdict,
        detail,
    )


# ── Issue claims (migration 033) ───────────────────────────────────

# Advisory-lock class for "one sync per fingerprint". The two-int lock form keeps these out
# of the single-bigint key space the migration runner's lock lives in.
_ISSUE_CLAIM_LOCK_CLASS = 0x0ADA0002


class _PgHeld:
    def __init__(self, conn, target: str, fp: str):
        self._conn = conn
        self._target = target
        self._fp = fp

    async def get(self) -> int | None:
        return await self._conn.fetchval(
            "SELECT issue_number FROM audit_issue_claims WHERE target = $1 AND fp = $2",
            self._target,
            self._fp,
        )

    async def record(self, number: int) -> None:
        await self._conn.execute(
            """
            INSERT INTO audit_issue_claims (target, fp, issue_number)
            VALUES ($1, $2, $3)
            ON CONFLICT (target, fp)
            DO UPDATE SET issue_number = EXCLUDED.issue_number, updated_at = NOW()
            """,
            self._target,
            self._fp,
            number,
        )

    async def forget(self) -> None:
        await self._conn.execute(
            "DELETE FROM audit_issue_claims WHERE target = $1 AND fp = $2",
            self._target,
            self._fp,
        )


class PgIssueClaims:
    """:class:`ada.comms.rest.audit_issue.IssueClaims` on Postgres: a session advisory lock
    per fingerprint, held on one pooled connection for the length of that fingerprint's
    sync, and the ``audit_issue_claims`` row it reads and writes. Every replica and worker
    sharing the database is serialised; a connection that dies releases its lock.

    ``target`` names the forge + repo the issue numbers belong to
    (``"<kind>:<base url>:<owner/name>"``).
    """

    durable = True

    def __init__(self, pool: asyncpg.Pool, *, target: str):
        self._pool = pool
        self._target = target

    @asynccontextmanager
    async def hold(self, fp: str) -> AsyncIterator[_PgHeld]:
        key = f"{self._target}|{fp}"
        async with self._pool.acquire() as conn:
            await conn.execute("SELECT pg_advisory_lock($1, hashtext($2))", _ISSUE_CLAIM_LOCK_CLASS, key)
            try:
                yield _PgHeld(conn, self._target, fp)
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1, hashtext($2))", _ISSUE_CLAIM_LOCK_CLASS, key)
