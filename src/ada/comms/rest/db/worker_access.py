"""Per-user worker pool access policy (migration 057).

Stores whether a non-admin user may enqueue jobs onto the server worker pool
globally (``enabled = True``) and/or for specific worker capabilities
(``allowed_capabilities``). Policy evaluation lives in
:mod:`ada.comms.rest.worker_access` so it can be tested without Postgres.
"""

from __future__ import annotations

from collections.abc import Iterable

import asyncpg


def _iso(ts) -> str | None:
    return ts.isoformat() if ts is not None and hasattr(ts, "isoformat") else ts


def _normalise_capabilities(capabilities: Iterable[str] | None) -> list[str]:
    if not capabilities:
        return []
    cleaned = {str(c).strip().lower() for c in capabilities if str(c).strip()}
    return sorted(cleaned)


def default_worker_access_row() -> dict:
    return {
        "enabled": False,
        "allowed_capabilities": [],
        "updated_by": None,
        "updated_at": None,
    }


def _row(r) -> dict:
    return {
        "enabled": bool(r["enabled"]),
        "allowed_capabilities": list(r["allowed_capabilities"] or []),
        "updated_by": r["updated_by"],
        "updated_at": _iso(r["updated_at"]),
    }


async def get_user_worker_access(pool: asyncpg.Pool, user_sub: str) -> dict:
    """Return the stored worker-access policy for ``user_sub``, or the default-deny policy."""
    row = await pool.fetchrow(
        """
        SELECT enabled, allowed_capabilities, updated_by, updated_at
        FROM user_worker_access
        WHERE user_sub = $1
        """,
        user_sub,
    )
    if row is None:
        return default_worker_access_row()
    return _row(row)


async def set_user_worker_access(
    pool: asyncpg.Pool,
    user_sub: str,
    *,
    enabled: bool,
    allowed_capabilities: Iterable[str] | None = None,
    updated_by: str | None = None,
) -> dict:
    """Create or replace ``user_sub``'s worker-pool access policy.

    Inserts a placeholder ``users`` row when ``user_sub`` has not signed in yet
    so the foreign key holds (deleting the user deletes its worker-access row).
    """
    caps = _normalise_capabilities(allowed_capabilities)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("INSERT INTO users (sub) VALUES ($1) ON CONFLICT (sub) DO NOTHING", user_sub)
            row = await conn.fetchrow(
                """
                INSERT INTO user_worker_access (user_sub, enabled, allowed_capabilities, updated_by, updated_at)
                VALUES ($1, $2, $3::text[], $4, NOW())
                ON CONFLICT (user_sub) DO UPDATE SET
                    enabled = EXCLUDED.enabled,
                    allowed_capabilities = EXCLUDED.allowed_capabilities,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
                RETURNING enabled, allowed_capabilities, updated_by, updated_at
                """,
                user_sub,
                bool(enabled),
                caps,
                updated_by,
            )
    return _row(row)


async def list_user_worker_access(pool: asyncpg.Pool) -> dict[str, dict]:
    """Return all explicit worker-access rows keyed by ``user_sub``."""
    rows = await pool.fetch(
        """
        SELECT user_sub, enabled, allowed_capabilities, updated_by, updated_at
        FROM user_worker_access
        ORDER BY user_sub
        """
    )
    return {r["user_sub"]: _row(r) for r in rows}
