"""The global execution policy and per-user overrides (migration 058).

Documents are validated by :mod:`ada.comms.rest.execution_policy` before they get
here; this module only stores and reads them.
"""

from __future__ import annotations

import json

import asyncpg

from ._common import _loads_jsonb


def _iso(ts) -> str | None:
    return ts.isoformat() if ts is not None and hasattr(ts, "isoformat") else ts


def _row(r) -> dict:
    return {
        "policy": _loads_jsonb(r["policy"]) or {},
        "updated_by": r["updated_by"],
        "updated_at": _iso(r["updated_at"]),
    }


async def get_execution_policy(pool: asyncpg.Pool) -> dict | None:
    """The stored global policy row, or None (the defaults apply)."""
    r = await pool.fetchrow("SELECT policy, updated_by, updated_at FROM execution_policy WHERE id = 1")
    return _row(r) if r is not None else None


async def set_execution_policy(pool: asyncpg.Pool, policy: dict, *, updated_by: str | None = None) -> dict:
    """Replace the global policy document."""
    r = await pool.fetchrow(
        """
        INSERT INTO execution_policy (id, policy, updated_by, updated_at)
        VALUES (1, $1::jsonb, $2, NOW())
        ON CONFLICT (id) DO UPDATE SET
            policy = EXCLUDED.policy,
            updated_by = EXCLUDED.updated_by,
            updated_at = NOW()
        RETURNING policy, updated_by, updated_at
        """,
        json.dumps(policy),
        updated_by,
    )
    return _row(r)


async def delete_execution_policy(pool: asyncpg.Pool) -> bool:
    """Drop the stored global policy (back to the defaults)."""
    status = await pool.execute("DELETE FROM execution_policy WHERE id = 1")
    return int(status.split()[-1]) > 0


async def get_user_execution_policy(pool: asyncpg.Pool, user_sub: str) -> dict | None:
    """``user_sub``'s override row, or None when the user has none."""
    r = await pool.fetchrow(
        "SELECT policy, updated_by, updated_at FROM user_execution_policy WHERE user_sub = $1",
        user_sub,
    )
    return _row(r) if r is not None else None


async def set_user_execution_policy(
    pool: asyncpg.Pool, user_sub: str, policy: dict, *, updated_by: str | None = None
) -> dict:
    """Create or replace ``user_sub``'s override.

    Inserts a placeholder ``users`` row when the user has not signed in yet so the
    foreign key holds (deleting the user deletes the override)."""
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("INSERT INTO users (sub) VALUES ($1) ON CONFLICT (sub) DO NOTHING", user_sub)
            r = await conn.fetchrow(
                """
                INSERT INTO user_execution_policy (user_sub, policy, updated_by, updated_at)
                VALUES ($1, $2::jsonb, $3, NOW())
                ON CONFLICT (user_sub) DO UPDATE SET
                    policy = EXCLUDED.policy,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
                RETURNING policy, updated_by, updated_at
                """,
                user_sub,
                json.dumps(policy),
                updated_by,
            )
    return _row(r)


async def delete_user_execution_policy(pool: asyncpg.Pool, user_sub: str) -> bool:
    """Clear ``user_sub``'s override; False when there was none."""
    status = await pool.execute("DELETE FROM user_execution_policy WHERE user_sub = $1", user_sub)
    return int(status.split()[-1]) > 0
