"""Audit corpora (named collections of source files)."""

from __future__ import annotations

import asyncpg

# ── Corpora (M3 admin audit panel) ──────────────────────────────────


def _corpus_row(r) -> dict:
    return {
        "id": str(r["id"]),
        "slug": r["slug"],
        "name": r["name"],
        "description": r["description"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "created_by": r["created_by"],
        "archived_at": (r["archived_at"].isoformat() if r["archived_at"] else None),
    }


async def list_corpora(
    pool: asyncpg.Pool,
    *,
    include_archived: bool = False,
) -> list[dict]:
    """Newest-first corpora list. Archived rows hidden by default; the
    admin panel never needs them and the audit-form picker would
    confuse the operator by listing slugs that storage will reject."""
    where = "" if include_archived else " WHERE archived_at IS NULL"
    rows = await pool.fetch(
        f"""
        SELECT id, slug, name, description, created_at,
               created_by, archived_at
        FROM corpora
        {where}
        ORDER BY created_at DESC
        """
    )
    return [_corpus_row(r) for r in rows]


async def create_corpus(
    pool: asyncpg.Pool,
    *,
    slug: str,
    name: str,
    description: str | None = None,
    created_by: str | None = None,
) -> dict:
    """Insert one new corpus. ``slug`` is the human-readable id used
    on the wire (``corpus:<slug>``); enforce uniqueness against live
    rows via the partial unique index. Archived rows holding the
    same slug are no obstacle."""
    row = await pool.fetchrow(
        """
        INSERT INTO corpora (slug, name, description, created_by)
        VALUES ($1, $2, $3, $4)
        RETURNING id, slug, name, description, created_at,
                  created_by, archived_at
        """,
        slug,
        name,
        description,
        created_by,
    )
    return _corpus_row(row)


async def update_corpus(
    pool: asyncpg.Pool,
    slug: str,
    *,
    name: str,
    description: str | None,
) -> dict | None:
    """Update a live corpus's display name / description. The slug is
    immutable — it's baked into the storage prefix (``corpus/<slug>/``)
    and scope URLs, so renaming it would orphan the bucket bytes.
    Returns the updated row, or None when the slug isn't live."""
    row = await pool.fetchrow(
        """
        UPDATE corpora
        SET name = $2, description = $3
        WHERE slug = $1 AND archived_at IS NULL
        RETURNING id, slug, name, description, created_at,
                  created_by, archived_at
        """,
        slug,
        name,
        description,
    )
    return _corpus_row(row) if row else None


async def get_corpus_by_slug(
    pool: asyncpg.Pool,
    slug: str,
) -> dict | None:
    """Look up one corpus by its public slug. Returns the live row
    only; archived corpora are treated as absent."""
    row = await pool.fetchrow(
        """
        SELECT id, slug, name, description, created_at,
               created_by, archived_at
        FROM corpora
        WHERE slug = $1 AND archived_at IS NULL
        """,
        slug,
    )
    return _corpus_row(row) if row else None


async def archive_corpus(pool: asyncpg.Pool, slug: str) -> bool:
    """Soft-delete a corpus. Storage bytes stay in the bucket — the
    operator wipes those separately if disk pressure matters. The
    slug becomes available for re-use immediately because the
    uniqueness index is partial-on-live."""
    result = await pool.execute(
        """
        UPDATE corpora
        SET archived_at = NOW()
        WHERE slug = $1 AND archived_at IS NULL
        """,
        slug,
    )
    return result.endswith(" 1")
