"""Apply-changes cursors: one row per (provider, collection), the ``next_cursor`` the provider last
answered and core then applied. Read and advanced by ``routes/asset_apply_changes.py``; see
migration 046 for the why.

THE CURSOR MOVES LAST. It is written only after every patch of a collection's answer has been
written, so a missing or older row is always safe: the next run asks the same question again, and
every patch is an idempotent upsert or delete.
"""

from __future__ import annotations

from typing import Optional

import asyncpg

from .asset_changes import _iso


def _row(r) -> dict:
    return {
        "provider": r["provider"],
        "collection": r["collection"],
        "cursor": r["cursor"],
        "applied_at": _iso(r["applied_at"]),
        "run_id": str(r["run_id"]) if r["run_id"] else None,
    }


async def get_asset_apply_cursor(pool: asyncpg.Pool, *, provider: str, collection: str) -> Optional[dict]:
    """The stored cursor of (provider, collection), or None -- the next run is a baseline."""
    row = await pool.fetchrow(
        "SELECT provider, collection, cursor, applied_at, run_id FROM asset_apply_cursors "
        "WHERE provider = $1 AND collection = $2",
        provider,
        collection.lower(),
    )
    return _row(row) if row else None


async def set_asset_apply_cursor(
    pool: asyncpg.Pool, *, provider: str, collection: str, cursor: str, run_id: Optional[str] = None
) -> dict:
    """Store ``cursor`` for (provider, collection), replacing what was there."""
    row = await pool.fetchrow(
        """
        INSERT INTO asset_apply_cursors (provider, collection, cursor, applied_at, run_id)
        VALUES ($1, $2, $3, NOW(), $4)
        ON CONFLICT (provider, collection) DO UPDATE SET
            cursor = EXCLUDED.cursor, applied_at = EXCLUDED.applied_at, run_id = EXCLUDED.run_id
        RETURNING provider, collection, cursor, applied_at, run_id
        """,
        provider,
        collection.lower(),
        cursor,
        run_id,
    )
    return _row(row)


async def list_asset_apply_cursors(pool: asyncpg.Pool, *, provider: Optional[str] = None) -> list[dict]:
    rows = await pool.fetch(
        "SELECT provider, collection, cursor, applied_at, run_id FROM asset_apply_cursors "
        "WHERE ($1::text IS NULL OR provider = $1) ORDER BY provider, collection",
        provider,
    )
    return [_row(r) for r in rows]


async def delete_asset_apply_cursor(pool: asyncpg.Pool, *, provider: str, collection: str) -> bool:
    """Forget the cursor: the next run of (provider, collection) is a baseline again. True when
    there was one."""
    status = await pool.execute(
        "DELETE FROM asset_apply_cursors WHERE provider = $1 AND collection = $2", provider, collection.lower()
    )
    return status.split()[-1] != "0"
