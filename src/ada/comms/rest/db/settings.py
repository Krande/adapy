"""Key/value app settings."""

from __future__ import annotations

import asyncpg

# ── App settings ─────────────────────────────────────────────────────


async def get_setting(pool: asyncpg.Pool, key: str) -> str | None:
    row = await pool.fetchrow("SELECT value FROM app_settings WHERE key = $1", key)
    return row["value"] if row else None


async def set_setting(pool: asyncpg.Pool, key: str, value: str, *, updated_by: str | None) -> None:
    """Upsert a setting. ``value`` is a string — caller serializes
    booleans / numbers as appropriate (we keep this small and avoid
    type-tagging columns)."""
    await pool.execute(
        """
        INSERT INTO app_settings (key, value, updated_by)
        VALUES ($1, $2, $3)
        ON CONFLICT (key) DO UPDATE SET
            value = EXCLUDED.value,
            updated_at = NOW(),
            updated_by = EXCLUDED.updated_by
        """,
        key,
        value,
        updated_by,
    )
