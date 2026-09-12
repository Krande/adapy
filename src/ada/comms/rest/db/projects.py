"""Projects, users and project membership."""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg


@dataclass(frozen=True)
class Project:
    id: str
    slug: str
    name: str
    role: str  # caller's role within this project


# ── Repository helpers ────────────────────────────────────────────────


async def upsert_user(pool: asyncpg.Pool, sub: str, email: str, display_name: str) -> None:
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
    return [Project(id=str(r["id"]), slug=r["slug"], name=r["name"], role=r["role"]) for r in rows]


async def is_project_member(pool: asyncpg.Pool, project_id: str, user_sub: str) -> bool:
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


async def add_project_member(pool: asyncpg.Pool, project_id: str, user_sub: str, role: str = "member") -> bool:
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


async def remove_project_member(pool: asyncpg.Pool, project_id: str, user_sub: str) -> bool:
    row = await pool.fetchrow(
        "DELETE FROM project_members WHERE project_id = $1 AND user_sub = $2 RETURNING user_sub",
        project_id,
        user_sub,
    )
    return row is not None


async def project_exists(pool: asyncpg.Pool, project_id: str) -> bool:
    row = await pool.fetchrow("SELECT 1 FROM projects WHERE id = $1", project_id)
    return row is not None


async def project_id_from_slug(pool: asyncpg.Pool, slug: str) -> str | None:
    """Resolve a project slug to its UUID. Returns None if no match.

    Slugs are URL-safe and stable across renames of the human-readable
    name; UUIDs are the FK-stable id. The scope URL parser uses this to
    accept ``project:<slug>`` as a friendlier alternative to
    ``project:<uuid>``.
    """
    row = await pool.fetchrow(
        "SELECT id FROM projects WHERE slug = $1 AND archived_at IS NULL",
        slug,
    )
    return str(row["id"]) if row else None
