"""Provider sweeps: one ``asset_sweep_runs`` row per firing, one ``asset_sweep_items`` row per
collection in it. Driven by ``routes/asset_sweeps.py``; see migration 036 for the why.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import asyncpg

from ..run_driver import RunBeat
from .asset_changes import _iso, _loads

#: A ``running`` sweep whose driver has not stamped its heartbeat for this long is reported as
#: ``interrupted``: its API process is gone, and nothing else will finish it.
HEARTBEAT_STALE_S = 300


def _run_row(r) -> dict:
    status = r["status"]
    if status == "running" and r["stale"]:
        status = "interrupted"
    return {
        "id": str(r["id"]),
        "scope": r["scope"],
        "provider": r["provider"],
        "plugin_id": r["plugin_id"],
        "job": r["job"],
        "settings": _loads(r["settings"]) or {},
        "schedule_id": str(r["schedule_id"]) if r["schedule_id"] else None,
        "requested_by": r["requested_by"],
        "requested_via": r["requested_via"],
        "status": status,
        "total": r["total"],
        "created_at": _iso(r["created_at"]),
        "heartbeat_at": _iso(r["heartbeat_at"]),
        "finished_at": _iso(r["finished_at"]),
        "error": r["error"],
        # A pending "skip current" (migration 038): the position the driver has not acted on yet.
        "skip_position": r["skip_position"],
        # Run-level notes and facts (migration 047): an apply-changes run's skipped collections and
        # the full-export run it queued; a follow-up run's link back.
        "warnings": _loads(r["warnings"]) or [],
        "summary": _loads(r["summary"]) or {},
    }


def _item_row(r) -> dict:
    return {
        "position": r["position"],
        "collection": r["collection"],
        "status": r["status"],
        "fetch_job_id": r["fetch_job_id"],
        "publish_job_id": r["publish_job_id"],
        "revision": r["revision"],
        "message": r["message"],
        "error": r["error"],
        "timings": _loads(r["timings"]) or {},
        "started_at": _iso(r["started_at"]),
        "finished_at": _iso(r["finished_at"]),
    }


_RUN_COLS = (
    "id, scope, provider, plugin_id, job, settings, schedule_id, requested_by, requested_via, status, total, "
    "created_at, heartbeat_at, finished_at, error, skip_position, warnings, summary, "
    f"(heartbeat_at < NOW() - INTERVAL '{HEARTBEAT_STALE_S} seconds') AS stale"
)
_ITEM_COLS = (
    "position, collection, status, fetch_job_id, publish_job_id, revision, message, error, timings, "
    "started_at, finished_at"
)


async def insert_asset_sweep_run(
    pool: asyncpg.Pool,
    *,
    scope: str,
    provider: str,
    plugin_id: str,
    job: str,
    settings: dict,
    collections: list[str],
    requested_by: Optional[str],
    requested_via: str,
    schedule_id: Optional[str] = None,
    excluded: frozenset[str] = frozenset(),
    summary: Optional[dict] = None,
) -> dict:
    """The run and its items in one transaction: a sweep is listed whole or not at all. Items for
    ``excluded`` collections start ``excluded`` instead of ``pending``: not run unless someone
    ticks them back on while the sweep has not reached them. ``summary`` seeds the run's facts
    (a follow-up run's ``apply_changes_run_id``)."""
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            f"""
            INSERT INTO asset_sweep_runs
                (scope, provider, plugin_id, job, settings, schedule_id, requested_by, requested_via, total, summary)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10::jsonb)
            RETURNING {_RUN_COLS}
            """,
            scope,
            provider,
            plugin_id,
            job,
            json.dumps(settings or {}),
            schedule_id,
            requested_by,
            requested_via,
            len(collections),
            json.dumps(summary or {}),
        )
        await conn.executemany(
            "INSERT INTO asset_sweep_items (run_id, position, collection, status, message) VALUES ($1, $2, $3, $4, $5)",
            [
                (row["id"], i, c, *(("excluded", EXCLUDED_BY_DEFAULT) if c in excluded else ("pending", None)))
                for i, c in enumerate(collections)
            ],
        )
    return _run_row(row)


#: The message on an item a provider's default list left out of the sweep.
EXCLUDED_BY_DEFAULT = "not run: unticked in the provider's default sweep list"


async def set_asset_sweep_item_run(pool: asyncpg.Pool, run_id: str, position: int, *, run: bool) -> Optional[str]:
    """Tick an item of a running sweep on (``pending``) or off (``excluded``). Only an item the
    sweep has not reached moves -- one in flight is skipped, not excluded. Returns the item's new
    status, or None when it was not in a state that can move."""
    new, old = ("pending", "excluded") if run else ("excluded", "pending")
    return await pool.fetchval(
        """
        UPDATE asset_sweep_items i SET status = $3, message = $5
        FROM asset_sweep_runs r
        WHERE i.run_id = $1 AND i.position = $2 AND i.status = $4 AND r.id = i.run_id AND r.status = 'running'
        RETURNING i.status
        """,
        run_id,
        position,
        new,
        old,
        None if run else "not run: unticked during the sweep",
    )


async def asset_sweep_item_status(pool: asyncpg.Pool, run_id: str, position: int) -> Optional[str]:
    return await pool.fetchval(
        "SELECT status FROM asset_sweep_items WHERE run_id = $1 AND position = $2", run_id, position
    )


async def running_asset_sweep(pool: asyncpg.Pool, *, provider: str) -> Optional[dict]:
    """The provider's sweep still being driven, if any -- the concurrent-fire guard for sweeps."""
    row = await pool.fetchrow(
        f"""
        SELECT {_RUN_COLS} FROM asset_sweep_runs
        WHERE provider = $1 AND status = 'running'
          AND heartbeat_at >= NOW() - INTERVAL '{HEARTBEAT_STALE_S} seconds'
        ORDER BY created_at DESC LIMIT 1
        """,
        provider,
    )
    return _run_row(row) if row else None


async def heartbeat_asset_sweep_run(pool: asyncpg.Pool, run_id: str) -> Optional[str]:
    """Stamp the heartbeat; returns the run's status, so the driver sees a cancel -- as a
    :class:`RunBeat`, which also carries a pending "skip current" request."""
    row = await pool.fetchrow(
        """
        UPDATE asset_sweep_runs SET heartbeat_at = NOW() WHERE id = $1
        RETURNING status, skip_position, skip_requested_by
        """,
        run_id,
    )
    if row is None:
        return None
    return RunBeat(row["status"], row["skip_position"], row["skip_requested_by"])


async def request_asset_sweep_skip(pool: asyncpg.Pool, run_id: str, position: int, *, by: Optional[str]) -> bool:
    """Ask the driver to skip the item at ``position``. False when the run is not running."""
    row = await pool.fetchrow(
        """
        UPDATE asset_sweep_runs SET skip_position = $2, skip_requested_by = $3
        WHERE id = $1 AND status = 'running' RETURNING id
        """,
        run_id,
        position,
        by,
    )
    return row is not None


async def clear_asset_sweep_skip(pool: asyncpg.Pool, run_id: str, position: int) -> None:
    """The driver acted on the skip of ``position``; a newer request (another position) stays."""
    await pool.execute(
        """
        UPDATE asset_sweep_runs SET skip_position = NULL, skip_requested_by = NULL
        WHERE id = $1 AND skip_position = $2
        """,
        run_id,
        position,
    )


async def finish_asset_sweep_run(pool: asyncpg.Pool, run_id: str, *, status: str, error: Optional[str] = None) -> None:
    """Only a ``running`` run moves: a cancel that landed first is not overwritten."""
    await pool.execute(
        """
        UPDATE asset_sweep_runs SET status = $2, error = $3, finished_at = NOW(), heartbeat_at = NOW()
        WHERE id = $1 AND status = 'running'
        """,
        run_id,
        status,
        error,
    )


async def add_asset_sweep_run_warnings(pool: asyncpg.Pool, run_id: str, warnings: list[str]) -> None:
    """Append ``warnings`` to the run's list (a sentence already there is not added twice)."""
    if not warnings:
        return
    await pool.execute(
        """
        UPDATE asset_sweep_runs SET warnings = warnings || (
            SELECT COALESCE(jsonb_agg(w), '[]'::jsonb) FROM jsonb_array_elements($2::jsonb) w
            WHERE NOT asset_sweep_runs.warnings @> jsonb_build_array(w)
        ) WHERE id = $1
        """,
        run_id,
        json.dumps(list(dict.fromkeys(warnings))),
    )


async def merge_asset_sweep_run_summary(pool: asyncpg.Pool, run_id: str, summary: dict) -> None:
    """Merge ``summary``'s keys into the run's (a key given replaces the stored one)."""
    await pool.execute(
        "UPDATE asset_sweep_runs SET summary = summary || $2::jsonb WHERE id = $1", run_id, json.dumps(summary)
    )


async def cancel_asset_sweep_run(pool: asyncpg.Pool, run_id: str) -> bool:
    row = await pool.fetchrow(
        """
        UPDATE asset_sweep_runs SET status = 'cancelled', finished_at = NOW()
        WHERE id = $1 AND status = 'running' RETURNING id
        """,
        run_id,
    )
    return row is not None


#: The message on an item that was in flight when its sweep's driver died, and runs again on resume.
RESUMED_ITEM = "runs again: it was in flight when the sweep was interrupted"


async def resume_asset_sweep_run(pool: asyncpg.Pool, run_id: str) -> Optional[list[dict]]:
    """Re-arm an INTERRUPTED sweep (``running`` with a stale heartbeat) for a new driver.

    Stamps the heartbeat -- so the run reads ``running`` again and a second resume of it is refused
    -- clears a skip request nobody acted on, and puts every item that was in flight back to
    ``pending``: its job's answer was never recorded, so the collection runs again from the start.
    Done, failed and skipped items are kept as they are. Returns the items that were in flight
    (their old job ids, for the caller to cancel), or None when the run is not interrupted -- not
    running at all, or its driver is still alive."""
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            f"""
            UPDATE asset_sweep_runs SET heartbeat_at = NOW(), skip_position = NULL, skip_requested_by = NULL
            WHERE id = $1 AND status = 'running'
              AND heartbeat_at < NOW() - INTERVAL '{HEARTBEAT_STALE_S} seconds'
            RETURNING id
            """,
            run_id,
        )
        if row is None:
            return None
        rows = await conn.fetch(
            """
            UPDATE asset_sweep_items i SET status = 'pending', message = $2, error = NULL,
                started_at = NULL, finished_at = NULL, worker_slot = FALSE
            FROM (SELECT position, fetch_job_id, publish_job_id FROM asset_sweep_items
                  WHERE run_id = $1 AND (status IN ('fetching', 'publishing', 'waiting') OR worker_slot)
                  FOR UPDATE) old
            WHERE i.run_id = $1 AND i.position = old.position
            RETURNING i.position, i.collection, old.fetch_job_id, old.publish_job_id
            """,
            run_id,
            RESUMED_ITEM,
        )
    return [dict(r) for r in rows]


async def update_asset_sweep_item(pool: asyncpg.Pool, run_id: str, position: int, **fields: Any) -> None:
    allowed = (
        "status",
        "fetch_job_id",
        "publish_job_id",
        "revision",
        "message",
        "error",
        "timings",
        "started_at",
        "finished_at",
    )
    sets, args = [], [run_id, position]
    for name, value in fields.items():
        if name not in allowed:
            raise ValueError(f"not an asset_sweep_items column: {name}")
        args.append(json.dumps(value) if name == "timings" else value)
        sets.append(f"{name} = ${len(args)}{'::jsonb' if name == 'timings' else ''}")
    if not sets:
        return
    await pool.execute(
        f"UPDATE asset_sweep_items SET {', '.join(sets)} WHERE run_id = $1 AND position = $2",
        *args,
    )


async def skip_pending_asset_sweep_items(pool: asyncpg.Pool, run_id: str, reason: str) -> None:
    # `excluded` items stay `excluded`: they were never going to run, and saying "cancelled"
    # over them would lose who left them out.
    # A `waiting` item (another run held its collection) had not started either.
    await pool.execute(
        "UPDATE asset_sweep_items SET status = 'skipped', message = $2, worker_slot = FALSE "
        "WHERE run_id = $1 AND status IN ('pending', 'waiting')",
        run_id,
        reason,
    )


# --- several runs of one provider at once (migration 048) -----------------------------------------

#: Item states in which a run is working on its collection: no other run may claim it meanwhile.
IN_FLIGHT = ("fetching", "publishing")


def _live_runs(alias: str = "r") -> str:
    """SQL: the run is being driven (``running`` with a fresh heartbeat). A dead driver holds nothing."""
    return f"{alias}.status = 'running' AND {alias}.heartbeat_at >= NOW() - INTERVAL '{HEARTBEAT_STALE_S} seconds'"


async def claim_asset_sweep_item(
    pool: asyncpg.Pool, run_id: str, position: int, *, provider: str, collection: str
) -> dict:
    """Claim the item's collection for this run: move the item to ``fetching`` unless another live
    run of ``provider`` is working on the same collection (case-insensitive). Serialised per
    (provider, collection) by a transaction-scoped advisory lock, so two runs -- on two API replicas
    too -- can never both claim it. Returns ``{"claimed": bool, "status": the item's status,
    "holder": {run_id, job, created_at} | None}``; an item no longer ``pending`` / ``waiting`` (ticked
    off meanwhile) is not claimed and has no holder."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"asset_sweep:{provider}/{collection.lower()}"
        )
        status = await conn.fetchval(
            "SELECT status FROM asset_sweep_items WHERE run_id = $1 AND position = $2 FOR UPDATE", run_id, position
        )
        if status not in ("pending", "waiting"):
            return {"claimed": False, "status": status, "holder": None}
        holder = await conn.fetchrow(
            f"""
            SELECT r.id, r.job, r.created_at FROM asset_sweep_items i JOIN asset_sweep_runs r ON r.id = i.run_id
            WHERE r.provider = $1 AND r.id <> $2 AND lower(i.collection) = lower($3)
              AND i.status = ANY($4::text[]) AND {_live_runs()}
            ORDER BY r.created_at LIMIT 1
            """,
            provider,
            run_id,
            collection,
            list(IN_FLIGHT),
        )
        if holder is not None:
            return {
                "claimed": False,
                "status": status,
                "holder": {"run_id": str(holder["id"]), "job": holder["job"], "created_at": _iso(holder["created_at"])},
            }
        await conn.execute(
            "UPDATE asset_sweep_items SET status = 'fetching', message = NULL, started_at = NOW() "
            "WHERE run_id = $1 AND position = $2",
            run_id,
            position,
        )
    return {"claimed": True, "status": "fetching", "holder": None}


async def acquire_asset_worker_slot(pool: asyncpg.Pool, run_id: str, position: int, *, provider: str, cap: int) -> bool:
    """Take one of ``provider``'s worker slots for the item -- only while fewer than ``cap`` items of
    the provider's LIVE runs hold one. Serialised per provider by a transaction-scoped advisory lock,
    so the budget holds across runs and API replicas. True when taken (or already held)."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"asset_worker_slots:{provider}")
        row = await conn.fetchrow(
            f"""
            SELECT count(*) FILTER (WHERE NOT (i.run_id = $2 AND i.position = $3)) AS others,
                   COALESCE(bool_or(i.run_id = $2 AND i.position = $3), FALSE) AS mine
            FROM asset_sweep_items i JOIN asset_sweep_runs r ON r.id = i.run_id
            WHERE r.provider = $1 AND i.worker_slot AND {_live_runs()}
            """,
            provider,
            run_id,
            position,
        )
        if row["mine"]:
            return True
        if row["others"] >= cap:
            return False
        await conn.execute(
            "UPDATE asset_sweep_items SET worker_slot = TRUE WHERE run_id = $1 AND position = $2", run_id, position
        )
    return True


async def release_asset_worker_slot(pool: asyncpg.Pool, run_id: str, position: int) -> None:
    await pool.execute(
        "UPDATE asset_sweep_items SET worker_slot = FALSE WHERE run_id = $1 AND position = $2", run_id, position
    )


async def asset_worker_slots_in_use(pool: asyncpg.Pool, *, provider: str) -> int:
    """How many of ``provider``'s worker slots its live runs hold now -- for the admin view."""
    return int(
        await pool.fetchval(
            f"""
            SELECT count(*) FROM asset_sweep_items i JOIN asset_sweep_runs r ON r.id = i.run_id
            WHERE r.provider = $1 AND i.worker_slot AND {_live_runs()}
            """,
            provider,
        )
        or 0
    )


async def list_asset_sweep_runs(pool: asyncpg.Pool, *, provider: Optional[str] = None, limit: int = 20) -> list[dict]:
    """Newest first, each with a per-status count of its items."""
    rows = await pool.fetch(
        f"""
        SELECT {_RUN_COLS} FROM asset_sweep_runs
        WHERE ($1::text IS NULL OR provider = $1)
        ORDER BY created_at DESC LIMIT $2
        """,
        provider,
        limit,
    )
    runs = [_run_row(r) for r in rows]
    if runs:
        counts = await pool.fetch(
            """
            SELECT run_id, status, COUNT(*) AS n FROM asset_sweep_items
            WHERE run_id = ANY($1::uuid[]) GROUP BY run_id, status
            """,
            [r["id"] for r in runs],
        )
        by_run: dict[str, dict[str, int]] = {}
        for c in counts:
            by_run.setdefault(str(c["run_id"]), {})[c["status"]] = int(c["n"])
        for run in runs:
            run["counts"] = by_run.get(run["id"], {})
    return runs


async def get_asset_sweep_run(pool: asyncpg.Pool, run_id: str) -> Optional[dict]:
    row = await pool.fetchrow(f"SELECT {_RUN_COLS} FROM asset_sweep_runs WHERE id = $1", run_id)
    if row is None:
        return None
    run = _run_row(row)
    items = await pool.fetch(f"SELECT {_ITEM_COLS} FROM asset_sweep_items WHERE run_id = $1 ORDER BY position", run_id)
    run["items"] = [_item_row(i) for i in items]
    return run
