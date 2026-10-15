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
    "created_at, heartbeat_at, finished_at, error, skip_position, "
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
) -> dict:
    """The run and its items in one transaction: a sweep is listed whole or not at all. Items for
    ``excluded`` collections start ``excluded`` instead of ``pending``: not run unless someone
    ticks them back on while the sweep has not reached them."""
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            f"""
            INSERT INTO asset_sweep_runs
                (scope, provider, plugin_id, job, settings, schedule_id, requested_by, requested_via, total)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, $7, $8, $9)
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
                started_at = NULL, finished_at = NULL
            FROM (SELECT position, fetch_job_id, publish_job_id FROM asset_sweep_items
                  WHERE run_id = $1 AND status IN ('fetching', 'publishing') FOR UPDATE) old
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
    await pool.execute(
        "UPDATE asset_sweep_items SET status = 'skipped', message = $2 WHERE run_id = $1 AND status = 'pending'",
        run_id,
        reason,
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
