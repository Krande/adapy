"""Change-check runs: one row per provider change check, scheduled or started from the Sources tab.

The job's summary carries the provider-neutral ``asset_changes`` block (schema
``ada.assets/changes@1``). A run is inserted ``queued`` when its job is enqueued and finished by
the API the first time someone lists runs after the job is done (see
``routes/asset_changes.py``): the worker that runs a plugin job need not have a database pool,
so the API, which always does, is where the headline is copied in.
"""

from __future__ import annotations

import json
from typing import Any, Optional

import asyncpg


def _loads(v):
    if v is None or isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return None


def _iso(v) -> Optional[str]:
    return v.isoformat() if v is not None else None


def _run_row(r, *, with_items: bool = False) -> dict:
    out = {
        "id": str(r["id"]),
        "scope": r["scope"],
        "provider": r["provider"],
        "collection": r["collection"],
        "plugin_id": r["plugin_id"],
        "schedule_id": str(r["schedule_id"]) if r["schedule_id"] else None,
        "job_id": r["job_id"],
        "requested_by": r["requested_by"],
        "requested_via": r["requested_via"],
        "status": r["status"],
        "created_at": _iso(r["created_at"]),
        "finished_at": _iso(r["finished_at"]),
        "stale": r["stale"],
        "up_to_date": r["up_to_date"],
        "message": r["message"],
        "counts": _loads(r["counts"]),
        "users": _loads(r["users"]) or [],
        "since": r["since"],
        "checked_at": r["checked_at"],
        "has_items": bool(r["items_key"]) or r["items"] is not None,
        "error": r["error"],
    }
    if with_items:
        out["derived_key"] = r["derived_key"]
        out["items_key"] = r["items_key"]
        out["items"] = _loads(r["items"])
    return out


_COLS = (
    "id, scope, provider, collection, plugin_id, schedule_id, job_id, derived_key, requested_by, "
    "requested_via, status, created_at, finished_at, stale, up_to_date, message, counts, users, "
    "since, checked_at, items_key, items, error"
)


async def insert_asset_change_run(
    pool: asyncpg.Pool,
    *,
    scope: str,
    provider: str,
    collection: str,
    plugin_id: str,
    job_id: Optional[str],
    derived_key: Optional[str],
    requested_by: Optional[str],
    requested_via: str,
    schedule_id: Optional[str] = None,
) -> dict:
    row = await pool.fetchrow(
        f"""
        INSERT INTO asset_change_runs
            (scope, provider, collection, plugin_id, schedule_id, job_id, derived_key, requested_by, requested_via)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        RETURNING {_COLS}
        """,
        scope,
        provider,
        collection,
        plugin_id,
        schedule_id,
        job_id,
        derived_key,
        requested_by,
        requested_via,
    )
    return _run_row(row)


async def list_asset_change_runs(
    pool: asyncpg.Pool,
    *,
    scope: str,
    provider: Optional[str] = None,
    collection: Optional[str] = None,
    limit: int = 50,
) -> list[dict]:
    """Newest first. ``with_items`` fields included, so the caller can finish pending runs."""
    rows = await pool.fetch(
        f"""
        SELECT {_COLS} FROM asset_change_runs
        WHERE scope = $1
          AND ($2::text IS NULL OR provider = $2)
          AND ($3::text IS NULL OR collection = $3)
        ORDER BY created_at DESC
        LIMIT $4
        """,
        scope,
        provider,
        collection,
        limit,
    )
    return [_run_row(r, with_items=True) for r in rows]


async def get_asset_change_run(pool: asyncpg.Pool, run_id: str) -> Optional[dict]:
    row = await pool.fetchrow(f"SELECT {_COLS} FROM asset_change_runs WHERE id = $1", run_id)
    return _run_row(row, with_items=True) if row else None


async def latest_asset_change_runs_for_schedules(pool: asyncpg.Pool, schedule_ids: list[str]) -> dict[str, dict]:
    """The newest run of each schedule, by schedule id -- the Providers tab's "last result"."""
    if not schedule_ids:
        return {}
    rows = await pool.fetch(
        f"""
        SELECT DISTINCT ON (schedule_id) {_COLS} FROM asset_change_runs
        WHERE schedule_id = ANY($1::uuid[])
        ORDER BY schedule_id, created_at DESC
        """,
        schedule_ids,
    )
    return {str(r["schedule_id"]): _run_row(r, with_items=True) for r in rows}


async def finish_asset_change_run(
    pool: asyncpg.Pool,
    run_id: str,
    *,
    status: str,
    block: Optional[dict[str, Any]] = None,
    error: Optional[str] = None,
) -> Optional[dict]:
    """Copy a finished job's ``asset_changes`` headline into the run. Idempotent: only a
    ``queued`` run moves, so two listings racing to finish one run write it once."""
    block = block or {}
    items = block.get("items")
    row = await pool.fetchrow(
        f"""
        UPDATE asset_change_runs
        SET status = $2,
            finished_at = NOW(),
            stale = $3,
            up_to_date = $4,
            message = $5,
            counts = $6::jsonb,
            users = $7::jsonb,
            since = $8,
            checked_at = $9,
            items_key = $10,
            items = $11::jsonb,
            error = $12
        WHERE id = $1 AND status = 'queued'
        RETURNING {_COLS}
        """,
        run_id,
        status,
        block.get("stale") if isinstance(block.get("stale"), bool) else None,
        block.get("up_to_date") if isinstance(block.get("up_to_date"), bool) else None,
        str(block["message"]) if block.get("message") is not None else None,
        json.dumps(block["counts"]) if isinstance(block.get("counts"), dict) else None,
        json.dumps([str(u) for u in block.get("users") or [] if u]) if block.get("users") else None,
        str(block["since"]) if block.get("since") else None,
        str(block["checked_at"]) if block.get("checked_at") else None,
        str(block["items_key"]) if block.get("items_key") else None,
        json.dumps(items) if isinstance(items, list) else None,
        error,
    )
    return _run_row(row, with_items=True) if row else None


async def asset_change_users(pool: asyncpg.Pool, *, scope: str, provider: str, collection: str) -> list[str]:
    """Every distinct ``changed_by`` earlier runs reported for this scope and collection --
    the choices for a setting declared with ``choices: "change_users"``."""
    rows = await pool.fetch(
        """
        SELECT DISTINCT jsonb_array_elements_text(users) AS u FROM asset_change_runs
        WHERE scope = $1 AND provider = $2 AND collection = $3 AND users IS NOT NULL
        """,
        scope,
        provider,
        collection,
    )
    return sorted(str(r["u"]) for r in rows if r["u"])
