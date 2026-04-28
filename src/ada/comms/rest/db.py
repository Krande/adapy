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


# ── Admin queries ────────────────────────────────────────────────────


async def list_audit(
    pool: asyncpg.Pool,
    *,
    user_sub: str | None = None,
    scope_kind: str | None = None,
    scope_id: str | None = None,
    action: str | None = None,
    limit: int = 100,
    before_id: int | None = None,
) -> list[dict]:
    """Reverse-chronological audit_log scan, optionally filtered.

    Pagination is keyset-style on ``id`` (the BIGSERIAL primary key) —
    pass the smallest id from the previous page as ``before_id``. id
    monotonicity matches ``ts`` ordering and avoids the offset-based
    "page drift" surprise when new rows arrive between requests.
    """
    where: list[str] = []
    args: list = []
    if user_sub:
        args.append(user_sub)
        where.append(f"user_sub = ${len(args)}")
    if scope_kind:
        args.append(scope_kind)
        where.append(f"scope_kind = ${len(args)}")
    if scope_id:
        args.append(scope_id)
        where.append(f"scope_id = ${len(args)}")
    if action:
        args.append(action)
        where.append(f"action = ${len(args)}")
    if before_id is not None:
        args.append(before_id)
        where.append(f"id < ${len(args)}")
    args.append(min(max(limit, 1), 500))
    sql = (
        "SELECT id, ts, user_sub, scope_kind, scope_id, action, key,"
        " target_format, status, error, duration_ms FROM audit_log"
    )
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY id DESC LIMIT ${len(args)}"
    rows = await pool.fetch(sql, *args)
    return [
        {
            "id": r["id"],
            "ts": r["ts"].isoformat() if r["ts"] is not None else None,
            "user_sub": r["user_sub"],
            "scope_kind": r["scope_kind"],
            "scope_id": r["scope_id"],
            "action": r["action"],
            "key": r["key"],
            "target_format": r["target_format"],
            "status": r["status"],
            "error": r["error"],
            "duration_ms": r["duration_ms"],
        }
        for r in rows
    ]


async def list_all_projects(pool: asyncpg.Pool) -> list[dict]:
    """Admin view: every project (including archived). Member count too."""
    rows = await pool.fetch(
        """
        SELECT p.id, p.slug, p.name, p.created_at, p.archived_at,
               COUNT(m.user_sub) AS member_count
        FROM projects p
        LEFT JOIN project_members m ON m.project_id = p.id
        GROUP BY p.id
        ORDER BY p.archived_at IS NOT NULL, p.name
        """
    )
    return [
        {
            "id": str(r["id"]),
            "slug": r["slug"],
            "name": r["name"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "archived_at": r["archived_at"].isoformat() if r["archived_at"] else None,
            "member_count": int(r["member_count"]),
        }
        for r in rows
    ]


async def create_project(pool: asyncpg.Pool, slug: str, name: str) -> dict:
    """Insert a project and return it. Slug is unique; conflicts → ValueError."""
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO projects (slug, name) VALUES ($1, $2)
            RETURNING id, slug, name, created_at, archived_at
            """,
            slug,
            name,
        )
    except asyncpg.UniqueViolationError as exc:
        raise ValueError(f"slug {slug!r} already exists") from exc
    assert row is not None
    return {
        "id": str(row["id"]),
        "slug": row["slug"],
        "name": row["name"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "archived_at": row["archived_at"].isoformat() if row["archived_at"] else None,
        "member_count": 0,
    }


async def archive_project(pool: asyncpg.Pool, project_id: str) -> bool:
    """Soft-delete: stamp archived_at. Returns False when not found.

    Soft delete preserves audit_log scope_id references and lets us
    un-archive without orphaning. Hard-delete is intentionally not
    exposed via the admin API.
    """
    row = await pool.fetchrow(
        "UPDATE projects SET archived_at = NOW() WHERE id = $1 AND archived_at IS NULL RETURNING id",
        project_id,
    )
    return row is not None


async def list_project_members(pool: asyncpg.Pool, project_id: str) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT m.user_sub, m.role, m.added_at,
               u.email, u.display_name, u.last_seen_at
        FROM project_members m
        LEFT JOIN users u ON u.sub = m.user_sub
        WHERE m.project_id = $1
        ORDER BY u.display_name, m.user_sub
        """,
        project_id,
    )
    return [
        {
            "user_sub": r["user_sub"],
            "role": r["role"],
            "added_at": r["added_at"].isoformat() if r["added_at"] else None,
            "email": r["email"],
            "display_name": r["display_name"],
            "last_seen_at": r["last_seen_at"].isoformat() if r["last_seen_at"] else None,
        }
        for r in rows
    ]


async def add_project_member(
    pool: asyncpg.Pool, project_id: str, user_sub: str, role: str = "member"
) -> bool:
    """Idempotent membership add. Returns True on insert, False on duplicate.

    Inserts a placeholder ``users`` row when the sub hasn't been seen
    yet so the FK holds — the row gets enriched (email, display_name)
    on the user's first authenticated request.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO users (sub) VALUES ($1) ON CONFLICT (sub) DO NOTHING",
                user_sub,
            )
            row = await conn.fetchrow(
                """
                INSERT INTO project_members (project_id, user_sub, role)
                VALUES ($1, $2, $3)
                ON CONFLICT (project_id, user_sub) DO NOTHING
                RETURNING user_sub
                """,
                project_id,
                user_sub,
                role,
            )
    return row is not None


async def remove_project_member(
    pool: asyncpg.Pool, project_id: str, user_sub: str
) -> bool:
    row = await pool.fetchrow(
        "DELETE FROM project_members WHERE project_id = $1 AND user_sub = $2 RETURNING user_sub",
        project_id,
        user_sub,
    )
    return row is not None


async def project_exists(pool: asyncpg.Pool, project_id: str) -> bool:
    row = await pool.fetchrow("SELECT 1 FROM projects WHERE id = $1", project_id)
    return row is not None
