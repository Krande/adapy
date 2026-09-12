"""Audit schedules: cron-style triggers that create audit runs."""

from __future__ import annotations

import asyncpg

# ── Audit schedules (M4 admin audit panel) ─────────────────────────


def _audit_schedule_row(r) -> dict:
    """Project an audit_schedules row to its JSON-ready dict shape."""
    return {
        "id": str(r["id"]),
        "name": r["name"],
        "cron_expr": r["cron_expr"],
        "scope": r["scope"],
        "worker_pool": r["worker_pool"],
        "enabled": r["enabled"],
        "last_fired_at": (r["last_fired_at"].isoformat() if r["last_fired_at"] else None),
        "next_fire_at": (r["next_fire_at"].isoformat() if r["next_fire_at"] else None),
        "last_skipped_reason": r["last_skipped_reason"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "created_by": r["created_by"],
        "archived_at": (r["archived_at"].isoformat() if r["archived_at"] else None),
    }


_SCHEDULE_COLS = (
    "id, name, cron_expr, scope, worker_pool, enabled, "
    "last_fired_at, next_fire_at, last_skipped_reason, "
    "created_at, created_by, archived_at"
)


async def list_audit_schedules(
    pool: asyncpg.Pool,
    *,
    include_archived: bool = False,
) -> list[dict]:
    """Newest-first audit_schedules listing. Archived rows hidden by
    default — the admin panel's primary view should only see live
    schedules."""
    where = "" if include_archived else " WHERE archived_at IS NULL"
    rows = await pool.fetch(f"SELECT {_SCHEDULE_COLS} FROM audit_schedules{where} " "ORDER BY created_at DESC")
    return [_audit_schedule_row(r) for r in rows]


async def get_audit_schedule(
    pool: asyncpg.Pool,
    schedule_id: str,
) -> dict | None:
    row = await pool.fetchrow(
        f"SELECT {_SCHEDULE_COLS} FROM audit_schedules WHERE id = $1",
        schedule_id,
    )
    return _audit_schedule_row(row) if row else None


async def create_audit_schedule(
    pool: asyncpg.Pool,
    *,
    name: str,
    cron_expr: str,
    scope: str,
    worker_pool: str | None,
    next_fire_at,
    enabled: bool = True,
    created_by: str | None = None,
) -> dict:
    """Insert one new audit_schedule. ``next_fire_at`` is pre-computed
    by the caller via croniter — keeping the cron parsing in the
    route handler means the DB layer stays library-free and the 400
    path for malformed expressions is sharper."""
    row = await pool.fetchrow(
        f"""
        INSERT INTO audit_schedules
            (name, cron_expr, scope, worker_pool, enabled, next_fire_at, created_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        RETURNING {_SCHEDULE_COLS}
        """,
        name,
        cron_expr,
        scope,
        worker_pool,
        enabled,
        next_fire_at,
        created_by,
    )
    return _audit_schedule_row(row)


async def update_audit_schedule(
    pool: asyncpg.Pool,
    schedule_id: str,
    *,
    name: str | None = None,
    cron_expr: str | None = None,
    scope: str | None = None,
    worker_pool: str | None = None,
    worker_pool_set: bool = False,
    enabled: bool | None = None,
    next_fire_at=None,
    next_fire_at_set: bool = False,
) -> dict | None:
    """Partial update. Only fields with non-None values (and the
    ``*_set`` toggles for nullable columns where ``None`` is a
    meaningful payload value) are written; the rest are left at
    whatever the row already holds."""
    sets: list[str] = []
    args: list = []
    if name is not None:
        args.append(name)
        sets.append(f"name = ${len(args)}")
    if cron_expr is not None:
        args.append(cron_expr)
        sets.append(f"cron_expr = ${len(args)}")
    if scope is not None:
        args.append(scope)
        sets.append(f"scope = ${len(args)}")
    if worker_pool_set:
        args.append(worker_pool)
        sets.append(f"worker_pool = ${len(args)}")
    if enabled is not None:
        args.append(enabled)
        sets.append(f"enabled = ${len(args)}")
    if next_fire_at_set:
        args.append(next_fire_at)
        sets.append(f"next_fire_at = ${len(args)}")
    if not sets:
        return await get_audit_schedule(pool, schedule_id)
    args.append(schedule_id)
    row = await pool.fetchrow(
        f"""
        UPDATE audit_schedules
        SET {', '.join(sets)}
        WHERE id = ${len(args)} AND archived_at IS NULL
        RETURNING {_SCHEDULE_COLS}
        """,
        *args,
    )
    return _audit_schedule_row(row) if row else None


async def archive_audit_schedule(
    pool: asyncpg.Pool,
    schedule_id: str,
) -> bool:
    """Soft-delete one schedule. The tick query filters on
    ``archived_at IS NULL`` so the row stops firing immediately."""
    result = await pool.execute(
        "UPDATE audit_schedules SET archived_at = NOW() " "WHERE id = $1 AND archived_at IS NULL",
        schedule_id,
    )
    return result.endswith(" 1")


async def claim_due_audit_schedule(
    pool: asyncpg.Pool,
    *,
    now,
    next_fire_at,
):
    """Atomically claim ONE due schedule.

    Returns the claimed row (as a dict) or ``None`` if nothing was
    due. The UPDATE pins ``last_fired_at`` to the provided ``now``,
    advances ``next_fire_at`` to the supplied value (computed by the
    caller via croniter from ``now``), and clears
    ``last_skipped_reason`` — the caller can re-set the reason via
    :func:`set_audit_schedule_skip_reason` if dispatch ends up not
    firing.

    Uses ``FOR UPDATE SKIP LOCKED`` so multiple API replicas can tick
    in parallel without ever firing the same schedule twice.
    """
    row = await pool.fetchrow(
        f"""
        UPDATE audit_schedules
        SET last_fired_at = $1,
            next_fire_at = $2,
            last_skipped_reason = NULL
        WHERE id = (
            SELECT id FROM audit_schedules
            WHERE enabled
              AND archived_at IS NULL
              AND next_fire_at IS NOT NULL
              AND next_fire_at <= $1
            ORDER BY next_fire_at ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING {_SCHEDULE_COLS}
        """,
        now,
        next_fire_at,
    )
    return _audit_schedule_row(row) if row else None


async def set_audit_schedule_skip_reason(
    pool: asyncpg.Pool,
    schedule_id: str,
    reason: str,
) -> None:
    """Record why a tick decided not to dispatch (concurrent-fire
    guard, scope resolution failure, etc.). Stamped after the atomic
    claim so the operator sees the latest reason without unwinding
    the ``last_fired_at`` bump — the schedule still advances to the
    next slot rather than re-trying the missed one."""
    await pool.execute(
        "UPDATE audit_schedules SET last_skipped_reason = $2 WHERE id = $1",
        schedule_id,
        reason,
    )
