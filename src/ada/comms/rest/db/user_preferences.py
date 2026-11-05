"""Per-user preference documents (migrations 053, 054): one JSON value per (user, key),
deleted with the user."""

from __future__ import annotations

import json

import asyncpg

from ._common import _loads_jsonb


async def get_user_preference(pool: asyncpg.Pool, sub: str, key: str) -> object | None:
    """The stored value, or ``None`` when the user has none under ``key``."""
    v = await pool.fetchval("SELECT value FROM user_preferences WHERE user_sub = $1 AND key = $2", sub, key)
    return _loads_jsonb(v)


async def set_user_preference(pool: asyncpg.Pool, sub: str, key: str, value: object) -> None:
    """Replace the user's value under ``key``.

    Inserts a placeholder ``users`` row when the sub hasn't been seen yet so the
    foreign key holds (migration 054): deleting the user deletes its preferences.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("INSERT INTO users (sub) VALUES ($1) ON CONFLICT (sub) DO NOTHING", sub)
            await conn.execute(
                """
                INSERT INTO user_preferences (user_sub, key, value)
                VALUES ($1, $2, $3::jsonb)
                ON CONFLICT (user_sub, key) DO UPDATE SET
                    value = EXCLUDED.value,
                    updated_at = NOW()
                """,
                sub,
                key,
                json.dumps(value),
            )


async def delete_user_preference(pool: asyncpg.Pool, sub: str, key: str) -> bool:
    """Drop the user's value under ``key``; ``False`` when there was none."""
    status = await pool.execute("DELETE FROM user_preferences WHERE user_sub = $1 AND key = $2", sub, key)
    return int(status.split()[-1]) > 0
