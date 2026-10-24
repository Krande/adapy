"""Search-result exports: one ``asset_export_runs`` row per export, one ``asset_export_items`` row
per (provider, collection) batch of hits. Driven by ``routes/asset_exports.py``; see migration 038.

Also the export job's read of the attribute index (:func:`lookup_attr_rows`): the job knows which
elements it wrote only after reading the geometry, so it asks for their rows by node id, full name
and source ref -- the first two through the partition's btrees, the last only for what those left.
"""

from __future__ import annotations

import json
from typing import Any, Iterable, Optional

import asyncpg

from ..run_driver import RunBeat
from ._common import _loads_jsonb
from .asset_changes import _iso, _loads
from .attribute_index import SEARCH_STATEMENT_TIMEOUT, _ident, _partitions

#: As for sweeps: a running export whose driver has not stamped its heartbeat for this long is
#: reported as ``interrupted``.
HEARTBEAT_STALE_S = 300

_JSON_ITEM_COLS = ("timings", "files", "file_errors", "hits")


def _run_row(r) -> dict:
    status = r["status"]
    if status == "running" and r["stale"]:
        status = "interrupted"
    return {
        "id": str(r["id"]),
        "scope": r["scope"],
        "target_scope": r["target_scope"],
        "target_folder": r["target_folder"],
        "query": _loads(r["query"]),
        "options": _loads(r["options"]) or {},
        "hits": r["hits"],
        "requested_by": r["requested_by"],
        "status": status,
        "total": r["total"],
        "skip_position": r["skip_position"],
        "created_at": _iso(r["created_at"]),
        "heartbeat_at": _iso(r["heartbeat_at"]),
        "finished_at": _iso(r["finished_at"]),
        "error": r["error"],
    }


def _item_row(r, *, with_hits: bool = False) -> dict:
    hits = _loads(r["hits"]) or []
    out = {
        "position": r["position"],
        "provider": r["provider"],
        "collection": r["collection"],
        # Where the hits are read from (migration 041); null is the run's own scope.
        "origin_scope": r["origin_scope"],
        "hits": len(hits),
        "status": r["status"],
        "job_id": r["job_id"],
        "message": r["message"],
        "error": r["error"],
        "timings": _loads(r["timings"]) or {},
        "files": _loads(r["files"]) or {},
        "file_errors": _loads(r["file_errors"]) or [],
        "started_at": _iso(r["started_at"]),
        "finished_at": _iso(r["finished_at"]),
    }
    if with_hits:
        out["hit_rows"] = hits
    return out


_RUN_COLS = (
    "id, scope, target_scope, target_folder, query, options, hits, requested_by, status, total, skip_position, "
    "created_at, heartbeat_at, finished_at, error, "
    f"(heartbeat_at < NOW() - INTERVAL '{HEARTBEAT_STALE_S} seconds') AS stale"
)
_ITEM_COLS = (
    "position, provider, collection, origin_scope, hits, status, job_id, message, error, timings, files, file_errors, "
    "started_at, finished_at"
)


async def insert_asset_export_run(
    pool: asyncpg.Pool,
    *,
    scope: str,
    target_scope: str,
    target_folder: str,
    query: Any,
    options: dict,
    batches: list[tuple[str, str, list[dict]]],
    requested_by: Optional[str],
) -> dict:
    """The run and its items (one per ``(provider, collection, hits)`` batch), pending, in one
    transaction. An item's ``origin_scope`` is its hits' ``origin`` (they share one: see
    ``routes/asset_exports.batch_hits``) when that is not the run's ``scope``."""
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            f"""
            INSERT INTO asset_export_runs
                (scope, target_scope, target_folder, query, options, hits, requested_by, total)
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7, $8)
            RETURNING {_RUN_COLS}
            """,
            scope,
            target_scope,
            target_folder,
            json.dumps(query),
            json.dumps(options or {}),
            sum(len(h) for _, _, h in batches),
            requested_by,
            len(batches),
        )

        def origin(hits: list[dict]) -> Optional[str]:
            o = hits[0].get("origin") if hits else None
            return o if isinstance(o, str) and o and o != scope else None

        await conn.executemany(
            """
            INSERT INTO asset_export_items (run_id, position, provider, collection, origin_scope, hits)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            """,
            [(row["id"], i, p, c, origin(h), json.dumps(h)) for i, (p, c, h) in enumerate(batches)],
        )
    return _run_row(row)


async def heartbeat_asset_export_run(pool: asyncpg.Pool, run_id: str) -> Optional[str]:
    """Stamp the heartbeat; the run's status as a :class:`RunBeat` (with any pending skip)."""
    row = await pool.fetchrow(
        """
        UPDATE asset_export_runs SET heartbeat_at = NOW() WHERE id = $1
        RETURNING status, skip_position, skip_requested_by
        """,
        run_id,
    )
    if row is None:
        return None
    return RunBeat(row["status"], row["skip_position"], row["skip_requested_by"])


async def finish_asset_export_run(pool: asyncpg.Pool, run_id: str, *, status: str, error: Optional[str] = None) -> None:
    """Only a ``running`` run moves: a cancel that landed first is not overwritten."""
    await pool.execute(
        """
        UPDATE asset_export_runs SET status = $2, error = $3, finished_at = NOW(), heartbeat_at = NOW()
        WHERE id = $1 AND status = 'running'
        """,
        run_id,
        status,
        error,
    )


async def cancel_asset_export_run(pool: asyncpg.Pool, run_id: str) -> bool:
    row = await pool.fetchrow(
        """
        UPDATE asset_export_runs SET status = 'cancelled', finished_at = NOW()
        WHERE id = $1 AND status = 'running' RETURNING id
        """,
        run_id,
    )
    return row is not None


async def restart_asset_export_run(pool: asyncpg.Pool, run_id: str, positions: list[int]) -> bool:
    """Back to ``running`` with ``positions`` pending again -- unless it is running (and not
    interrupted) already: only one of two concurrent re-runs gets True."""
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            f"""
            UPDATE asset_export_runs
            SET status = 'running', error = NULL, finished_at = NULL, heartbeat_at = NOW(),
                skip_position = NULL, skip_requested_by = NULL
            WHERE id = $1
              AND (status <> 'running' OR heartbeat_at < NOW() - INTERVAL '{HEARTBEAT_STALE_S} seconds')
            RETURNING id
            """,
            run_id,
        )
        if row is None:
            return False
        await conn.execute(
            """
            UPDATE asset_export_items
            SET status = 'pending', job_id = NULL, message = 're-run queued', error = NULL,
                started_at = NULL, finished_at = NULL
            WHERE run_id = $1 AND position = ANY($2::int[])
            """,
            run_id,
            positions,
        )
    return True


async def delete_asset_export_run(pool: asyncpg.Pool, run_id: str) -> bool:
    """The run and its items, unless it is running (and not interrupted). The files stay."""
    row = await pool.fetchrow(
        f"""
        DELETE FROM asset_export_runs
        WHERE id = $1
          AND (status <> 'running' OR heartbeat_at < NOW() - INTERVAL '{HEARTBEAT_STALE_S} seconds')
        RETURNING id
        """,
        run_id,
    )
    return row is not None


async def request_asset_export_skip(pool: asyncpg.Pool, run_id: str, position: int, *, by: Optional[str]) -> bool:
    row = await pool.fetchrow(
        """
        UPDATE asset_export_runs SET skip_position = $2, skip_requested_by = $3
        WHERE id = $1 AND status = 'running' RETURNING id
        """,
        run_id,
        position,
        by,
    )
    return row is not None


async def clear_asset_export_skip(pool: asyncpg.Pool, run_id: str, position: int) -> None:
    await pool.execute(
        """
        UPDATE asset_export_runs SET skip_position = NULL, skip_requested_by = NULL
        WHERE id = $1 AND skip_position = $2
        """,
        run_id,
        position,
    )


async def update_asset_export_item(pool: asyncpg.Pool, run_id: str, position: int, **fields: Any) -> None:
    allowed = (
        "status",
        "job_id",
        "message",
        "error",
        "timings",
        "files",
        "file_errors",
        "started_at",
        "finished_at",
    )
    sets, args = [], [run_id, position]
    for name, value in fields.items():
        if name not in allowed:
            raise ValueError(f"not an asset_export_items column: {name}")
        is_json = name in _JSON_ITEM_COLS
        args.append(json.dumps(value) if is_json else value)
        sets.append(f"{name} = ${len(args)}{'::jsonb' if is_json else ''}")
    if not sets:
        return
    await pool.execute(
        f"UPDATE asset_export_items SET {', '.join(sets)} WHERE run_id = $1 AND position = $2",
        *args,
    )


async def skip_pending_asset_export_items(pool: asyncpg.Pool, run_id: str, reason: str) -> None:
    await pool.execute(
        "UPDATE asset_export_items SET status = 'skipped', message = $2 WHERE run_id = $1 AND status = 'pending'",
        run_id,
        reason,
    )


def _sum_files(items: Iterable[dict]) -> dict:
    total = {"written": 0, "no_geometry": 0, "failed": 0}
    for item in items:
        for k in total:
            total[k] += int((item.get("files") or {}).get(k) or 0)
    return total


async def list_asset_export_runs(pool: asyncpg.Pool, *, scope: str, limit: int = 20) -> list[dict]:
    """Newest first, each with a per-status count of its items and the summed file counts."""
    rows = await pool.fetch(
        f"SELECT {_RUN_COLS} FROM asset_export_runs WHERE scope = $1 ORDER BY created_at DESC LIMIT $2",
        scope,
        limit,
    )
    runs = [_run_row(r) for r in rows]
    if runs:
        items = await pool.fetch(
            "SELECT run_id, status, files FROM asset_export_items WHERE run_id = ANY($1::uuid[])",
            [r["id"] for r in runs],
        )
        counts: dict[str, dict[str, int]] = {}
        files: dict[str, list[dict]] = {}
        for i in items:
            rid = str(i["run_id"])
            counts.setdefault(rid, {})
            counts[rid][i["status"]] = counts[rid].get(i["status"], 0) + 1
            files.setdefault(rid, []).append({"files": _loads(i["files"]) or {}})
        for run in runs:
            run["counts"] = counts.get(run["id"], {})
            run["files"] = _sum_files(files.get(run["id"], []))
    return runs


async def get_asset_export_run(pool: asyncpg.Pool, run_id: str, *, with_hits: bool = False) -> Optional[dict]:
    row = await pool.fetchrow(f"SELECT {_RUN_COLS} FROM asset_export_runs WHERE id = $1", run_id)
    if row is None:
        return None
    run = _run_row(row)
    items = await pool.fetch(f"SELECT {_ITEM_COLS} FROM asset_export_items WHERE run_id = $1 ORDER BY position", run_id)
    run["items"] = [_item_row(i, with_hits=with_hits) for i in items]
    run["files"] = _sum_files(run["items"])
    return run


# --- the attribute index, as the export job reads it ---------------------------------------------


def _row_dict(r) -> dict:
    return {
        "node_id": r["node_id"],
        "parent_id": r["parent_id"],
        "full_name": r["full_name"],
        "source_ref": r["source_ref"],
        "kind": r["kind"],
        "depth": r["depth"],
        "attrs": _loads_jsonb(r["attrs"]) or {},
    }


async def lookup_attr_rows(
    pool: asyncpg.Pool,
    *,
    scope: str,
    provider: str,
    collection: str,
    node_ids: Iterable[str] = (),
    full_names: Iterable[str] = (),
    source_refs: Iterable[str] = (),
) -> list[dict]:
    """Index rows of one (scope, provider, collection) partition by node id, full name or source
    ref. Empty when the collection is not indexed. Full names are matched as given; the caller
    passes both spellings (with and without the leading ``/``) when it is unsure which one the
    provider wrote. Source refs are matched only for refs no row found by name or id answered,
    because that column has no index and the lookup is a scan."""
    node_ids = sorted({n for n in node_ids if n})
    full_names = sorted({n for n in full_names if n})
    source_refs = sorted({n for n in source_refs if n})
    if not (node_ids or full_names or source_refs):
        return []
    async with pool.acquire() as conn, conn.transaction(readonly=True):
        await conn.execute(f"SET LOCAL statement_timeout = '{SEARCH_STATEMENT_TIMEOUT}'")
        parts = await _partitions(conn, scope, [provider], [collection])
        if not parts:
            return []
        table = _ident(parts[0]["partition_name"])
        cols = "node_id, parent_id, full_name, source_ref, kind, depth, attrs"
        rows = [
            _row_dict(r)
            for r in await conn.fetch(
                f"SELECT {cols} FROM {table} WHERE node_id = ANY($1::text[]) OR full_name = ANY($2::text[])",
                node_ids,
                full_names,
            )
        ]
        found_refs = {r["source_ref"] for r in rows if r["source_ref"]}
        missing = [s for s in source_refs if s not in found_refs]
        if missing:
            rows.extend(
                _row_dict(r)
                for r in await conn.fetch(f"SELECT {cols} FROM {table} WHERE source_ref = ANY($1::text[])", missing)
            )
    return rows
