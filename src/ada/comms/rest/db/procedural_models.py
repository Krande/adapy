"""Procedural cell models and the per-scope procedural-engine registry."""

from __future__ import annotations

import json

import asyncpg

from ._common import _loads_jsonb

# ── Procedural cell models ───────────────────────────────────────────


def _procedural_row_summary(r) -> dict:
    out = {
        "id": str(r["id"]),
        "name": r["name"],
        "revision": r["revision"],
        "created_by": r["created_by"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
    }
    # The engine/schema_version columns were added later (migration 026); guard so
    # a summary built from a SELECT that omits them doesn't KeyError.
    if "engine" in r:
        out["engine"] = r["engine"]
    if "schema_version" in r:
        out["schema_version"] = r["schema_version"]
    return out


async def create_procedural_model(
    pool: asyncpg.Pool, *, scope_kind: str, scope_id: str | None, name: str, created_by: str | None
) -> dict | None:
    """Insert a new (empty) procedural model. Returns the full row incl. doc,
    or None when a live model with that name already exists in the scope."""
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO procedural_models (scope_kind, scope_id, name, created_by)
            VALUES ($1, $2, $3, $4)
            RETURNING id, name, doc, revision, engine, schema_version, created_by, created_at, updated_at
            """,
            scope_kind,
            scope_id,
            name,
            created_by,
        )
    except asyncpg.UniqueViolationError:
        return None
    out = _procedural_row_summary(row)
    out["doc"] = _loads_jsonb(row["doc"])
    return out


async def rename_procedural_model(pool: asyncpg.Pool, model_id: str, name: str) -> dict | None | bool:
    """Rename a model, which is also how it MOVES between folders.

    The name carries the folder path (see procedural.normalize_model_name), so
    there is one operation rather than two that could disagree about where a
    model lives.

    Returns the updated summary, ``False`` when the target name is taken (the
    scope-unique index — the same collision a filesystem would report), or
    ``None`` when the model does not exist or is archived.
    """
    try:
        row = await pool.fetchrow(
            """
            UPDATE procedural_models
            SET name = $2, updated_at = now()
            WHERE id = $1::uuid AND NOT archived
            RETURNING id, name, doc, revision, engine, schema_version, created_by, created_at, updated_at
            """,
            model_id,
            name,
        )
    except asyncpg.UniqueViolationError:
        return False
    if row is None:
        return None
    return _procedural_row_summary(row)


async def list_procedural_models(pool: asyncpg.Pool, *, scope_kind: str, scope_id: str | None) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT id, name, revision, engine, schema_version, created_by, created_at, updated_at
        FROM procedural_models
        WHERE scope_kind = $1 AND COALESCE(scope_id, '') = COALESCE($2, '') AND NOT archived
        ORDER BY name ASC
        """,
        scope_kind,
        scope_id,
    )
    return [_procedural_row_summary(r) for r in rows]


async def get_procedural_model(pool: asyncpg.Pool, model_id: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT id, scope_kind, scope_id, name, doc, revision, engine, schema_version,
               created_by, created_at, updated_at
        FROM procedural_models
        WHERE id = $1 AND NOT archived
        """,
        model_id,
    )
    if row is None:
        return None
    out = _procedural_row_summary(row)
    out["scope_kind"] = row["scope_kind"]
    out["scope_id"] = row["scope_id"]
    out["doc"] = _loads_jsonb(row["doc"])
    return out


async def update_procedural_model_doc(pool: asyncpg.Pool, model_id: str, doc: dict, base_revision: int) -> int | None:
    """Optimistic-concurrency doc update: bumps revision only when the caller's
    base_revision matches. Returns the new revision, or None on conflict. The
    engine/schema_version columns are mirrored from the doc's routing header so
    they stay the single source of truth."""
    row = await pool.fetchrow(
        """
        UPDATE procedural_models
        SET doc = $2::jsonb, revision = revision + 1, updated_at = now(),
            engine = COALESCE($4, engine), schema_version = COALESCE($5, schema_version)
        WHERE id = $1 AND revision = $3 AND NOT archived
        RETURNING revision
        """,
        model_id,
        json.dumps(doc),
        base_revision,
        doc.get("engine"),
        doc.get("schema_version"),
    )
    return None if row is None else row["revision"]


async def archive_procedural_model(pool: asyncpg.Pool, model_id: str) -> bool:
    res = await pool.execute(
        "UPDATE procedural_models SET archived = TRUE, updated_at = now() WHERE id = $1 AND NOT archived",
        model_id,
    )
    return res.endswith("1")


# ── Procedural-engine registry (per-scope) ───────────────────────────


def _engine_row_summary(r) -> dict:
    return {
        "id": str(r["id"]),
        "slug": r["slug"],
        "name": r["name"],
        "description": r["description"],
        "revision": r["revision"],
        "created_by": r["created_by"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
    }


async def create_procedural_engine(
    pool: asyncpg.Pool,
    *,
    scope_kind: str,
    scope_id: str | None,
    slug: str,
    name: str,
    description: str | None,
    created_by: str | None,
) -> dict | None:
    """Insert a new procedural engine (default builtin doc). Returns the full row
    incl. doc, or None when a live engine with that slug already exists."""
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO procedural_engines (scope_kind, scope_id, slug, name, description, created_by)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, slug, name, description, doc, revision, created_by, created_at, updated_at
            """,
            scope_kind,
            scope_id,
            slug,
            name,
            description,
            created_by,
        )
    except asyncpg.UniqueViolationError:
        return None
    out = _engine_row_summary(row)
    out["doc"] = _loads_jsonb(row["doc"])
    return out


async def list_procedural_engines(pool: asyncpg.Pool, *, scope_kind: str, scope_id: str | None) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT id, slug, name, description, revision, created_by, created_at, updated_at
        FROM procedural_engines
        WHERE scope_kind = $1 AND COALESCE(scope_id, '') = COALESCE($2, '') AND NOT archived
        ORDER BY name ASC
        """,
        scope_kind,
        scope_id,
    )
    return [_engine_row_summary(r) for r in rows]


async def get_procedural_engine(pool: asyncpg.Pool, engine_id: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT id, scope_kind, scope_id, slug, name, description, doc,
               revision, created_by, created_at, updated_at
        FROM procedural_engines
        WHERE id = $1 AND NOT archived
        """,
        engine_id,
    )
    if row is None:
        return None
    out = _engine_row_summary(row)
    out["scope_kind"] = row["scope_kind"]
    out["scope_id"] = row["scope_id"]
    out["doc"] = _loads_jsonb(row["doc"])
    return out


async def update_procedural_engine(
    pool: asyncpg.Pool,
    engine_id: str,
    *,
    slug: str,
    name: str,
    description: str | None,
    doc: dict,
    base_revision: int,
) -> int | None:
    """Optimistic-concurrency update of an engine's metadata + manifest doc.
    Returns the new revision, or None on revision conflict. Propagates
    asyncpg.UniqueViolationError when the new slug collides in-scope."""
    row = await pool.fetchrow(
        """
        UPDATE procedural_engines
        SET slug = $2, name = $3, description = $4, doc = $5::jsonb,
            revision = revision + 1, updated_at = now()
        WHERE id = $1 AND revision = $6 AND NOT archived
        RETURNING revision
        """,
        engine_id,
        slug,
        name,
        description,
        json.dumps(doc),
        base_revision,
    )
    return None if row is None else row["revision"]


async def get_procedural_engine_by_slug(
    pool: asyncpg.Pool, *, scope_kind: str, scope_id: str | None, slug: str
) -> dict | None:
    """A live engine (with its manifest ``doc``) by slug in a scope — the compile
    worker resolves a selected external engine's entrypoint/repo this way (the
    compile request carries only the slug)."""
    row = await pool.fetchrow(
        """
        SELECT id, scope_kind, scope_id, slug, name, description, doc,
               revision, created_by, created_at, updated_at
        FROM procedural_engines
        WHERE scope_kind = $1 AND COALESCE(scope_id, '') = COALESCE($2, '')
              AND slug = $3 AND NOT archived
        """,
        scope_kind,
        scope_id,
        slug,
    )
    if row is None:
        return None
    out = _engine_row_summary(row)
    out["scope_kind"] = row["scope_kind"]
    out["scope_id"] = row["scope_id"]
    out["doc"] = _loads_jsonb(row["doc"])
    return out


async def set_procedural_engine_wheel(pool: asyncpg.Pool, engine_id: str, wheel_key: str | None) -> bool:
    """Record (or clear) a built engine's wheel pointer in its manifest doc
    WITHOUT bumping the revision — a background build must not race the user's
    optimistic commits (mirrors :func:`apply_inferred_bbox`)."""
    res = await pool.execute(
        "UPDATE procedural_engines SET doc = jsonb_set(doc, '{wheel_key}', $2::jsonb, true), "
        "updated_at = now() WHERE id = $1 AND NOT archived",
        engine_id,
        json.dumps(wheel_key),
    )
    return res.endswith("1")


async def archive_procedural_engine(pool: asyncpg.Pool, engine_id: str) -> bool:
    res = await pool.execute(
        "UPDATE procedural_engines SET archived = TRUE, updated_at = now() WHERE id = $1 AND NOT archived",
        engine_id,
    )
    return res.endswith("1")
