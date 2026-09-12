"""Plugin-job schedules (cron for plugin jobs) and in-flight plugin-job lookups."""

from __future__ import annotations

import json
from typing import Optional

import asyncpg

from ada.config import logger

# ── Plugin-job schedules (cron for plugin jobs) ─────────────────────
#
# Deliberately shaped like the audit_schedules helpers above so the two read the
# same way side by side -- same claim strategy, same skip-reason convention. See
# migrations/029_plugin_job_schedules.sql for why they are separate tables.


def _plugin_job_schedule_row(r) -> dict:
    """Project a plugin_job_schedules row to its JSON-ready dict shape."""
    return {
        "id": str(r["id"]),
        "name": r["name"],
        "cron_expr": r["cron_expr"],
        "scope": r["scope"],
        "plugin_id": r["plugin_id"],
        # asyncpg hands JSONB back as a string unless a codec is registered, and
        # this module registers none. Decoded here so every caller sees a dict
        # rather than half of them re-parsing it.
        "options": (json.loads(r["options"]) if isinstance(r["options"], str) else (r["options"] or {})),
        "capability": r["capability"],
        "enabled": r["enabled"],
        "last_fired_at": (r["last_fired_at"].isoformat() if r["last_fired_at"] else None),
        "next_fire_at": (r["next_fire_at"].isoformat() if r["next_fire_at"] else None),
        "last_skipped_reason": r["last_skipped_reason"],
        "last_job_id": r["last_job_id"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "created_by": r["created_by"],
        "archived_at": (r["archived_at"].isoformat() if r["archived_at"] else None),
    }


_PLUGIN_JOB_SCHEDULE_COLS = (
    "id, name, cron_expr, scope, plugin_id, options, capability, enabled, "
    "last_fired_at, next_fire_at, last_skipped_reason, last_job_id, "
    "created_at, created_by, archived_at"
)


async def list_plugin_job_schedules(pool: asyncpg.Pool, *, include_archived: bool = False) -> list:
    where = "" if include_archived else " WHERE archived_at IS NULL"
    rows = await pool.fetch(
        f"SELECT {_PLUGIN_JOB_SCHEDULE_COLS} FROM plugin_job_schedules{where} ORDER BY created_at DESC"
    )
    return [_plugin_job_schedule_row(r) for r in rows]


async def get_plugin_job_schedule(pool: asyncpg.Pool, schedule_id: str) -> Optional[dict]:
    row = await pool.fetchrow(
        f"SELECT {_PLUGIN_JOB_SCHEDULE_COLS} FROM plugin_job_schedules WHERE id = $1",
        schedule_id,
    )
    return _plugin_job_schedule_row(row) if row else None


async def create_plugin_job_schedule(
    pool: asyncpg.Pool,
    *,
    name: str,
    cron_expr: str,
    scope: str,
    plugin_id: str,
    options: dict,
    capability: Optional[str] = None,
    enabled: bool = True,
    next_fire_at=None,
    created_by: Optional[str] = None,
) -> dict:
    """Insert one schedule. ``next_fire_at`` is pre-computed by the caller from
    ``cron_expr``, which is also where the expression is validated."""
    row = await pool.fetchrow(
        f"""
        INSERT INTO plugin_job_schedules
            (name, cron_expr, scope, plugin_id, options, capability, enabled, next_fire_at, created_by)
        VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
        RETURNING {_PLUGIN_JOB_SCHEDULE_COLS}
        """,
        name,
        cron_expr,
        scope,
        plugin_id,
        json.dumps(options or {}),
        capability,
        enabled,
        next_fire_at,
        created_by,
    )
    return _plugin_job_schedule_row(row)


async def update_plugin_job_schedule(pool: asyncpg.Pool, schedule_id: str, **fields) -> Optional[dict]:
    """Partial update. Only the named columns move.

    Built as a dynamic SET list rather than one statement per field, but the
    column names come from a FIXED allow-list -- a caller cannot name a column
    that is not here, so the interpolation below never carries a caller's string
    into the SQL.
    """
    allowed = (
        "name",
        "cron_expr",
        "scope",
        "plugin_id",
        "options",
        "capability",
        "enabled",
        "next_fire_at",
        "last_fired_at",
        "last_skipped_reason",
        "last_job_id",
    )
    sets, values = [], []
    for column in allowed:
        if column not in fields:
            continue
        value = fields[column]
        if column == "options":
            sets.append(f"{column} = ${len(values) + 1}::jsonb")
            values.append(json.dumps(value or {}))
        else:
            sets.append(f"{column} = ${len(values) + 1}")
            values.append(value)
    if not sets:
        return await get_plugin_job_schedule(pool, schedule_id)
    values.append(schedule_id)
    row = await pool.fetchrow(
        f"UPDATE plugin_job_schedules SET {', '.join(sets)} "
        f"WHERE id = ${len(values)} RETURNING {_PLUGIN_JOB_SCHEDULE_COLS}",
        *values,
    )
    return _plugin_job_schedule_row(row) if row else None


async def archive_plugin_job_schedule(pool: asyncpg.Pool, schedule_id: str) -> bool:
    """Soft delete. The audit rows a schedule produced outlive it, and archiving
    frees the name for re-use."""
    result = await pool.execute(
        "UPDATE plugin_job_schedules SET archived_at = NOW(), enabled = FALSE " "WHERE id = $1 AND archived_at IS NULL",
        schedule_id,
    )
    return result.endswith(" 1")


async def claim_due_plugin_job_schedule(pool: asyncpg.Pool, *, now, next_fire_at) -> Optional[dict]:
    """Atomically claim ONE due schedule, or None.

    ``FOR UPDATE SKIP LOCKED`` so several API replicas can tick concurrently and
    still fire a given row at most once -- the losers' UPDATE matches nothing and
    they move on. ``last_skipped_reason`` is cleared on claim; the caller re-sets
    it if the dispatch then decides not to fire.

    ``next_fire_at`` is a provisional value the caller corrects immediately after,
    once the row's own ``cron_expr`` is known. Claiming first and computing second
    is what keeps the claim a single statement.
    """
    row = await pool.fetchrow(
        f"""
        UPDATE plugin_job_schedules
        SET last_fired_at = $1,
            next_fire_at = $2,
            last_skipped_reason = NULL
        WHERE id = (
            SELECT id FROM plugin_job_schedules
            WHERE enabled
              AND archived_at IS NULL
              AND next_fire_at IS NOT NULL
              AND next_fire_at <= $1
            ORDER BY next_fire_at ASC
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        RETURNING {_PLUGIN_JOB_SCHEDULE_COLS}
        """,
        now,
        next_fire_at,
    )
    return _plugin_job_schedule_row(row) if row else None


async def set_plugin_job_schedule_skip_reason(pool: asyncpg.Pool, schedule_id: str, reason: str) -> None:
    """Record why a due slot produced no job.

    Best-effort: a schedule that fires matters more than the note explaining one
    that did not, so this never raises into the tick.
    """
    try:
        await pool.execute(
            "UPDATE plugin_job_schedules SET last_skipped_reason = $2 WHERE id = $1",
            schedule_id,
            reason,
        )
    except Exception:
        logger.exception("plugin-job scheduler: could not record skip reason for %s", schedule_id)


async def plugin_job_in_flight_jobs(pool: asyncpg.Pool, *, scope_kind: str, scope_id, plugin_id: str) -> list[str]:
    """Job ids for this plugin and scope whose audit row is still queued or running.

    The concurrent-fire guard's first pass, and the guard matters more for a plugin
    than for an audit sweep: a plugin job can hold a single licensed workstation
    for minutes, so two overlapping firings do not merely double the load -- they
    contend for one resource, and the loser tends to fail in a way that reads as
    the plugin's fault rather than as a scheduling one.

    IDS, NOT A BOOLEAN, because this row alone cannot answer the question. The
    terminal status is written by the WORKER, so a worker with no database pool
    cannot write it -- it says as much at startup -- and in that deployment every
    plugin job stays `queued` here for good. A boolean would then report a finished
    job as still in flight and block its schedule permanently after the first
    firing, which is a worse failure than the double-firing it set out to prevent.
    The caller checks these ids against the queue, which the worker does update.

    Matched on the synthetic source key's prefix, which is where the plugin id
    lives; audit_log carries no plugin column. ``starts_with`` rather than
    ``LIKE``: that key begins with an underscore, which LIKE reads as a
    single-character wildcard.

    Rows with no ``job_id`` are skipped: there is nothing to check them against,
    and one is not evidence of a running job -- it is an audit row that never got
    as far as being queued.
    """
    prefix = f"_synthetic/plugin_job/{plugin_id}/"
    rows = await pool.fetch(
        """
        SELECT job_id FROM audit_log
        WHERE target_format = 'plugin_job'
          AND status IN ('queued', 'running')
          AND scope_kind = $1
          AND scope_id IS NOT DISTINCT FROM $2
          AND starts_with(key, $3)
          AND job_id IS NOT NULL
        """,
        scope_kind,
        scope_id,
        prefix,
    )
    return [str(r["job_id"]) for r in rows]
