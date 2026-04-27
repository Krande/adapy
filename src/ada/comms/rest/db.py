"""Postgres layer for the multi-tenant REST viewer.

Optional. When ``DATABASE_URL`` is empty the pool stays ``None`` and
the API serves in shared-only mode: every authenticated user lands in
the same shared bucket, no projects, no admin panel, no audit log.
This keeps the helm chart genuinely "Postgres-optional" — small
deployments don't need to stand up a database to use the viewer.

Migrations are bundled SQL files under ``migrations/`` and applied at
boot inside an advisory lock so a multi-replica rollout doesn't race.
A row in ``schema_version`` records each applied file by stem name.

Repository helpers expose the small set of queries the rest of the
package needs (project list, membership check, audit log insert).
asyncpg's pool is held on FastAPI's ``app.state.db_pool`` and pulled
via the :func:`get_pool` accessor.
"""

from __future__ import annotations

import importlib.resources
from dataclasses import dataclass
from typing import Optional

import asyncpg

from ada.config import logger

# Postgres advisory-lock id for the migration runner. asyncpg / PG
# treat advisory keys as int8 (signed 64-bit), so the value must fit
# in [-2^63, 2^63). Top nibble cleared so this stays a positive int.
_MIGRATION_LOCK_ID = 0x0ADA0001_ADA00001


@dataclass(frozen=True)
class Project:
    id: str
    slug: str
    name: str
    role: str  # caller's role within this project


async def init_pool(database_url: str) -> Optional[asyncpg.Pool]:
    """Build a connection pool and apply pending migrations.

    Returns ``None`` when ``database_url`` is empty so callers can
    branch into shared-only mode without a try/except.
    """
    if not database_url:
        logger.info("db: DATABASE_URL not set — running in shared-only mode")
        return None
    pool = await asyncpg.create_pool(
        dsn=database_url,
        min_size=1,
        max_size=10,
        # Kill idle connections after 10 min — Postgres servers behind
        # load balancers (PgBouncer, Garage's PG, managed Postgres) are
        # finicky about long-idle conns.
        max_inactive_connection_lifetime=600.0,
    )
    try:
        await _apply_migrations(pool)
    except Exception:
        await pool.close()
        raise
    logger.info("db: pool ready, migrations up-to-date")
    return pool


async def close_pool(pool: Optional[asyncpg.Pool]) -> None:
    if pool is not None:
        await pool.close()


async def _apply_migrations(pool: asyncpg.Pool) -> None:
    """Run any unapplied migrations under a Postgres advisory lock.

    The lock is held only during apply; competing replicas wait, then
    discover the migrations are already applied and become a no-op.
    """
    files = sorted(
        p
        for p in importlib.resources.files("ada.comms.rest.migrations").iterdir()
        if p.name.endswith(".sql")
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
            applied = {
                r["version"]
                for r in await conn.fetch("SELECT version FROM schema_version")
            }
            for path in files:
                version = path.stem
                if version in applied:
                    continue
                logger.info("db: applying migration %s", version)
                sql = path.read_text(encoding="utf-8")
                async with conn.transaction():
                    await conn.execute(sql)
                    await conn.execute(
                        "INSERT INTO schema_version(version) VALUES ($1)", version
                    )
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", _MIGRATION_LOCK_ID)


# ── Repository helpers ────────────────────────────────────────────────


async def upsert_user(
    pool: asyncpg.Pool, sub: str, email: str, display_name: str
) -> None:
    """Lazy user upsert on first authenticated request. Bumps last_seen_at."""
    await pool.execute(
        """
        INSERT INTO users (sub, email, display_name)
        VALUES ($1, $2, $3)
        ON CONFLICT (sub) DO UPDATE SET
            email = EXCLUDED.email,
            display_name = EXCLUDED.display_name,
            last_seen_at = NOW()
        """,
        sub,
        email or None,
        display_name or None,
    )


async def list_user_projects(pool: asyncpg.Pool, user_sub: str) -> list[Project]:
    rows = await pool.fetch(
        """
        SELECT p.id, p.slug, p.name, m.role
        FROM project_members m
        JOIN projects p ON p.id = m.project_id
        WHERE m.user_sub = $1 AND p.archived_at IS NULL
        ORDER BY p.name
        """,
        user_sub,
    )
    return [
        Project(id=str(r["id"]), slug=r["slug"], name=r["name"], role=r["role"])
        for r in rows
    ]


async def is_project_member(
    pool: asyncpg.Pool, project_id: str, user_sub: str
) -> bool:
    row = await pool.fetchrow(
        """
        SELECT 1
        FROM project_members m
        JOIN projects p ON p.id = m.project_id
        WHERE m.user_sub = $1 AND p.id = $2 AND p.archived_at IS NULL
        """,
        user_sub,
        project_id,
    )
    return row is not None


async def insert_audit(
    pool: asyncpg.Pool,
    *,
    user_sub: str | None,
    scope_kind: str,
    scope_id: str | None,
    action: str,
    key: str | None = None,
    target_format: str | None = None,
    status: str | None = None,
    error: str | None = None,
    duration_ms: int | None = None,
) -> None:
    await pool.execute(
        """
        INSERT INTO audit_log
            (user_sub, scope_kind, scope_id, action, key,
             target_format, status, error, duration_ms)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        """,
        user_sub,
        scope_kind,
        scope_id,
        action,
        key,
        target_format,
        status,
        error,
        duration_ms,
    )
