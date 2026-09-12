"""Migration runner: bundled SQL files applied under an advisory lock."""

from __future__ import annotations

import importlib.resources

import asyncpg

from ada.config import logger

# Postgres advisory-lock id for the migration runner. asyncpg / PG
# treat advisory keys as int8 (signed 64-bit), so the value must fit
# in [-2^63, 2^63). Top nibble cleared so this stays a positive int.
_MIGRATION_LOCK_ID = 0x0ADA0001_ADA00001


async def _apply_migrations(pool: asyncpg.Pool) -> None:
    """Run any unapplied migrations under a Postgres advisory lock.

    The lock is held only during apply; competing replicas wait, then
    discover the migrations are already applied and become a no-op.
    """
    files = sorted(
        p for p in importlib.resources.files("ada.comms.rest.migrations").iterdir() if p.name.endswith(".sql")
    )

    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", _MIGRATION_LOCK_ID)
        try:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_version (
                    version    TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            applied = {r["version"] for r in await conn.fetch("SELECT version FROM schema_version")}
            for path in files:
                version = path.stem
                if version in applied:
                    continue
                logger.info("db: applying migration %s", version)
                sql = path.read_text(encoding="utf-8")
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute("INSERT INTO schema_version(version) VALUES ($1)", version)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _MIGRATION_LOCK_ID)
