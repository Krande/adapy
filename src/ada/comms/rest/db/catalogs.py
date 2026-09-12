"""Per-scope catalogs: equipment types and system templates, plus the catalog fingerprint."""

from __future__ import annotations

import hashlib
import json

import asyncpg

from ._common import _loads_jsonb

# ── Equipment-type catalog (per-scope) ───────────────────────────────


def _equipment_row_summary(r) -> dict:
    return {
        "id": str(r["id"]),
        "slug": r["slug"],
        "name": r["name"],
        "description": r["description"],
        "cad_key": r["cad_key"],
        "revision": r["revision"],
        "created_by": r["created_by"],
        "created_at": r["created_at"].isoformat() if r["created_at"] else None,
        "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
    }


async def create_equipment_type(
    pool: asyncpg.Pool,
    *,
    scope_kind: str,
    scope_id: str | None,
    slug: str,
    name: str,
    description: str | None,
    created_by: str | None,
) -> dict | None:
    """Insert a new equipment type (default doc). Returns the full row incl.
    doc, or None when a live type with that slug already exists in the scope."""
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO equipment_types (scope_kind, scope_id, slug, name, description, created_by)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING id, slug, name, description, doc, cad_key, revision, created_by, created_at, updated_at
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
    out = _equipment_row_summary(row)
    out["doc"] = _loads_jsonb(row["doc"])
    return out


async def list_equipment_types(pool: asyncpg.Pool, *, scope_kind: str, scope_id: str | None) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT id, slug, name, description, cad_key, revision, created_by, created_at, updated_at
        FROM equipment_types
        WHERE scope_kind = $1 AND COALESCE(scope_id, '') = COALESCE($2, '') AND NOT archived
        ORDER BY name ASC
        """,
        scope_kind,
        scope_id,
    )
    return [_equipment_row_summary(r) for r in rows]


async def get_equipment_docs_by_scope(pool: asyncpg.Pool, *, scope_kind: str, scope_id: str | None) -> dict[str, dict]:
    """Map ``slug -> doc`` for the live equipment types in a scope. Used by the
    procedural compiler to resolve a placed catalog equipment to its
    bbox/mass/ports definition."""
    rows = await pool.fetch(
        """
        SELECT slug, doc FROM equipment_types
        WHERE scope_kind = $1 AND COALESCE(scope_id, '') = COALESCE($2, '') AND NOT archived
        """,
        scope_kind,
        scope_id,
    )
    return {r["slug"]: _loads_jsonb(r["doc"]) for r in rows}


async def get_equipment_cad_keys_by_scope(
    pool: asyncpg.Pool, *, scope_kind: str, scope_id: str | None
) -> dict[str, str]:
    """Map ``slug -> cad_key`` for live equipment types in a scope that have a
    linked CAD asset. Used to splice real CAD geometry into a compiled model."""
    rows = await pool.fetch(
        """
        SELECT slug, cad_key FROM equipment_types
        WHERE scope_kind = $1 AND COALESCE(scope_id, '') = COALESCE($2, '')
          AND NOT archived AND cad_key IS NOT NULL
        """,
        scope_kind,
        scope_id,
    )
    return {r["slug"]: r["cad_key"] for r in rows}


async def get_catalog_fingerprint(pool: asyncpg.Pool, *, scope_kind: str, scope_id: str | None) -> str:
    """A short, stable fingerprint of a scope's EQUIPMENT + SYSTEM catalogs — the
    live inputs a procedural compile resolves that are NOT captured by the model's
    own revision. Aggregates every non-archived catalog item's ``slug:revision``
    (plus ``cad_key`` for equipment, since re-linking CAD changes the spliced
    geometry). The value therefore changes whenever a catalog item is added (a new
    slug appears), edited (its revision bumps), CAD-relinked, or archived (its slug
    drops out). Folded into the procedural compile cache decision (via a sidecar) so
    a catalog edit forces a fresh compile instead of serving the revision-stamped
    stale artifact. Empty catalogs hash to a stable constant (md5 of '')."""
    eq_fp = await pool.fetchval(
        """
        SELECT md5(COALESCE(
            string_agg(slug || ':' || revision::text || ':' || COALESCE(cad_key, ''), ',' ORDER BY slug),
            ''))
        FROM equipment_types
        WHERE scope_kind = $1 AND COALESCE(scope_id, '') = COALESCE($2, '') AND NOT archived
        """,
        scope_kind,
        scope_id,
    )
    sy_fp = await pool.fetchval(
        """
        SELECT md5(COALESCE(string_agg(slug || ':' || revision::text, ',' ORDER BY slug), ''))
        FROM system_templates
        WHERE scope_kind = $1 AND COALESCE(scope_id, '') = COALESCE($2, '') AND NOT archived
        """,
        scope_kind,
        scope_id,
    )
    return hashlib.sha256(f"eq:{eq_fp}|sy:{sy_fp}".encode("utf-8")).hexdigest()[:16]


async def get_equipment_type(pool: asyncpg.Pool, type_id: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT id, scope_kind, scope_id, slug, name, description, doc, cad_key,
               revision, created_by, created_at, updated_at
        FROM equipment_types
        WHERE id = $1 AND NOT archived
        """,
        type_id,
    )
    if row is None:
        return None
    out = _equipment_row_summary(row)
    out["scope_kind"] = row["scope_kind"]
    out["scope_id"] = row["scope_id"]
    out["doc"] = _loads_jsonb(row["doc"])
    return out


async def update_equipment_type(
    pool: asyncpg.Pool,
    type_id: str,
    *,
    slug: str,
    name: str,
    description: str | None,
    doc: dict,
    base_revision: int,
) -> int | None:
    """Optimistic-concurrency update of an equipment type's metadata + doc.
    Returns the new revision, or None on revision conflict. Propagates
    asyncpg.UniqueViolationError when the new slug collides in-scope."""
    row = await pool.fetchrow(
        """
        UPDATE equipment_types
        SET slug = $2, name = $3, description = $4, doc = $5::jsonb,
            revision = revision + 1, updated_at = now()
        WHERE id = $1 AND revision = $6 AND NOT archived
        RETURNING revision
        """,
        type_id,
        slug,
        name,
        description,
        json.dumps(doc),
        base_revision,
    )
    return None if row is None else row["revision"]


async def set_equipment_type_cad(pool: asyncpg.Pool, type_id: str, cad_key: str | None) -> bool:
    """Link (or clear) the equipment type's CAD asset. Orthogonal to the doc
    revision — the preview key is not revision-stamped."""
    res = await pool.execute(
        "UPDATE equipment_types SET cad_key = $2, updated_at = now() WHERE id = $1 AND NOT archived",
        type_id,
        cad_key,
    )
    return res.endswith("1")


async def apply_inferred_bbox(pool: asyncpg.Pool, type_id: str, bbox: dict) -> bool:
    """Merge a worker-inferred bounding box into the doc without bumping the
    revision (a background inference must not race the user's optimistic
    commits)."""
    res = await pool.execute(
        "UPDATE equipment_types SET doc = jsonb_set(doc, '{bbox}', $2::jsonb, true), "
        "updated_at = now() WHERE id = $1 AND NOT archived",
        type_id,
        json.dumps(bbox),
    )
    return res.endswith("1")


async def archive_equipment_type(pool: asyncpg.Pool, type_id: str) -> bool:
    res = await pool.execute(
        "UPDATE equipment_types SET archived = TRUE, updated_at = now() WHERE id = $1 AND NOT archived",
        type_id,
    )
    return res.endswith("1")


# ── System-template catalog (per-scope) ──────────────────────────────


def _system_row_summary(r) -> dict:
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


async def create_system_template(
    pool: asyncpg.Pool,
    *,
    scope_kind: str,
    scope_id: str | None,
    slug: str,
    name: str,
    description: str | None,
    created_by: str | None,
) -> dict | None:
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO system_templates (scope_kind, scope_id, slug, name, description, created_by)
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
    out = _system_row_summary(row)
    out["doc"] = _loads_jsonb(row["doc"])
    return out


async def list_system_templates(pool: asyncpg.Pool, *, scope_kind: str, scope_id: str | None) -> list[dict]:
    rows = await pool.fetch(
        """
        SELECT id, slug, name, description, revision, created_by, created_at, updated_at
        FROM system_templates
        WHERE scope_kind = $1 AND COALESCE(scope_id, '') = COALESCE($2, '') AND NOT archived
        ORDER BY name ASC
        """,
        scope_kind,
        scope_id,
    )
    return [_system_row_summary(r) for r in rows]


async def get_system_template(pool: asyncpg.Pool, template_id: str) -> dict | None:
    row = await pool.fetchrow(
        """
        SELECT id, scope_kind, scope_id, slug, name, description, doc,
               revision, created_by, created_at, updated_at
        FROM system_templates
        WHERE id = $1 AND NOT archived
        """,
        template_id,
    )
    if row is None:
        return None
    out = _system_row_summary(row)
    out["scope_kind"] = row["scope_kind"]
    out["scope_id"] = row["scope_id"]
    out["doc"] = _loads_jsonb(row["doc"])
    return out


async def update_system_template(
    pool: asyncpg.Pool,
    template_id: str,
    *,
    slug: str,
    name: str,
    description: str | None,
    doc: dict,
    base_revision: int,
) -> int | None:
    row = await pool.fetchrow(
        """
        UPDATE system_templates
        SET slug = $2, name = $3, description = $4, doc = $5::jsonb,
            revision = revision + 1, updated_at = now()
        WHERE id = $1 AND revision = $6 AND NOT archived
        RETURNING revision
        """,
        template_id,
        slug,
        name,
        description,
        json.dumps(doc),
        base_revision,
    )
    return None if row is None else row["revision"]


async def archive_system_template(pool: asyncpg.Pool, template_id: str) -> bool:
    res = await pool.execute(
        "UPDATE system_templates SET archived = TRUE, updated_at = now() WHERE id = $1 AND NOT archived",
        template_id,
    )
    return res.endswith("1")
