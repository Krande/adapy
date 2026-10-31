"""Provider grants (migration 045): which scope may read which provider dataset.

Provider data lives once, in the provider store (``Scope.providers()``). A grant maps a TARGET scope
(a storage prefix: ``shared``, ``projects/<uuid>``, ``users/<sub>``) to a dataset -- a provider and
one collection, or every collection of the provider (``collection`` None) -- with three separate
permissions: ``attributes`` (the attribute index), ``geometry`` (published blobs) and ``request``
(members may ask the provider for the dataset; the job still runs in the provider store).

A grant answers WHO may read: a member of the target scope reads what is granted to it, and never
needs access to the provider store. Revoking is immediate -- the read path asks per request.

When a provider-wide grant and a collection grant both apply, the effective access is their union
(:func:`merge_access`): a collection grant can add a permission, never take one away. Narrowing is
done by replacing the provider-wide grant with collection grants.

This module only stores and answers; the read path (resolving a scope's read to the provider store)
is phase 2 of ``docs/design/provider-scope-grants.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

import asyncpg

#: The permissions a grant carries, in the order the API reports them.
PERMISSIONS = ("attributes", "geometry", "request")


@dataclass(frozen=True)
class GrantAccess:
    """What a scope may do with one dataset. All False is "not granted"."""

    attributes: bool = False
    geometry: bool = False
    request: bool = False

    def __bool__(self) -> bool:
        return self.attributes or self.geometry or self.request

    def allows(self, permission: str) -> bool:
        if permission not in PERMISSIONS:
            raise ValueError(f"unknown permission {permission!r} (one of {', '.join(PERMISSIONS)})")
        return bool(getattr(self, permission))


def merge_access(rows: Iterable[dict]) -> GrantAccess:
    """The union of the grants that apply to one dataset (a provider-wide one and/or a collection one)."""
    out = GrantAccess()
    for r in rows:
        out = GrantAccess(
            attributes=out.attributes or bool(r.get("attributes")),
            geometry=out.geometry or bool(r.get("geometry")),
            request=out.request or bool(r.get("request")),
        )
    return out


def _norm_collection(collection: Optional[str]) -> Optional[str]:
    """Collections are kept lower-case, as the attribute index keys them; blank is "every one"."""
    if collection is None:
        return None
    c = str(collection).strip().lower()
    return c or None


def _row(r) -> dict:
    granted_at = r["granted_at"]
    return {
        "id": str(r["id"]),
        "target_scope": r["target_scope"],
        "provider": r["provider"],
        "collection": r["collection"],
        "attributes": r["attributes"],
        "geometry": r["geometry"],
        "request": r["request"],
        "granted_at": granted_at.isoformat() if hasattr(granted_at, "isoformat") else granted_at,
        "granted_by": r["granted_by"],
        "note": r["note"],
    }


_COLUMNS = "id, target_scope, provider, collection, attributes, geometry, request, granted_at, granted_by, note"


async def upsert_provider_grant(
    pool: asyncpg.Pool,
    *,
    target_scope: str,
    provider: str,
    collection: Optional[str] = None,
    attributes: bool = True,
    geometry: bool = True,
    request: bool = False,
    granted_by: Optional[str] = None,
    note: Optional[str] = None,
) -> dict:
    """Create the grant, or replace the permissions of the one that exists for the same
    (target, provider, collection). A grant with no permission at all is refused: that is a revoke."""
    if not (attributes or geometry or request):
        raise ValueError("a grant needs at least one permission; revoke it instead")
    row = await pool.fetchrow(
        f"""
        INSERT INTO provider_grants (target_scope, provider, collection, attributes, geometry, request, granted_by, note)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (target_scope, provider, COALESCE(collection, '')) DO UPDATE SET
            attributes = EXCLUDED.attributes, geometry = EXCLUDED.geometry, request = EXCLUDED.request,
            granted_at = NOW(), granted_by = EXCLUDED.granted_by,
            note = COALESCE(EXCLUDED.note, provider_grants.note)
        RETURNING {_COLUMNS}
        """,
        target_scope,
        provider,
        _norm_collection(collection),
        bool(attributes),
        bool(geometry),
        bool(request),
        granted_by,
        note,
    )
    return _row(row)


async def merge_provider_grant(
    conn,
    *,
    target_scope: str,
    provider: str,
    collection: Optional[str] = None,
    attributes: bool = True,
    geometry: bool = True,
    granted_by: Optional[str] = None,
    note: Optional[str] = None,
) -> bool:
    """Add permissions to the grant of (target, provider, collection), creating it when missing --
    never taking one away (the union, as :func:`merge_access` reads two grants). What a migration of
    older access rules writes, so running it twice, or over grants an admin already set, narrows
    nothing. True when a row was created or gained a permission."""
    if not (attributes or geometry):
        return False
    status = await conn.execute(
        """
        INSERT INTO provider_grants (target_scope, provider, collection, attributes, geometry, request, granted_by, note)
        VALUES ($1, $2, $3, $4, $5, FALSE, $6, $7)
        ON CONFLICT (target_scope, provider, COALESCE(collection, '')) DO UPDATE SET
            attributes = provider_grants.attributes OR EXCLUDED.attributes,
            geometry = provider_grants.geometry OR EXCLUDED.geometry
        WHERE (EXCLUDED.attributes AND NOT provider_grants.attributes)
           OR (EXCLUDED.geometry AND NOT provider_grants.geometry)
        """,
        target_scope,
        provider,
        _norm_collection(collection),
        bool(attributes),
        bool(geometry),
        granted_by,
        note,
    )
    return status.split()[-1] != "0"


async def set_collection_grants(
    pool: asyncpg.Pool,
    *,
    target_scope: str,
    provider: str,
    grant: Iterable[str] = (),
    revoke: Iterable[str] = (),
    granted_by: Optional[str] = None,
) -> dict:
    """The Access tab's checkboxes, in one transaction: each collection in ``grant`` becomes READABLE
    by ``target_scope`` (attributes and geometry; a narrower grant is widened), each in ``revoke``
    loses its collection grant. Provider-wide grants are left alone either way -- they are their own
    checkbox. A collection in both lists is granted. ``{granted, revoked}``: what changed."""
    wanted = sorted({c for c in (_norm_collection(x) for x in grant) if c})
    dropped = sorted({c for c in (_norm_collection(x) for x in revoke) if c} - set(wanted))
    granted = revoked = 0
    async with pool.acquire() as conn, conn.transaction():
        for collection in wanted:
            status = await conn.execute(
                """
                INSERT INTO provider_grants (target_scope, provider, collection, attributes, geometry, request, granted_by)
                VALUES ($1, $2, $3, TRUE, TRUE, FALSE, $4)
                ON CONFLICT (target_scope, provider, COALESCE(collection, '')) DO UPDATE SET
                    attributes = TRUE, geometry = TRUE, granted_at = NOW(), granted_by = EXCLUDED.granted_by
                WHERE NOT (provider_grants.attributes AND provider_grants.geometry)
                """,
                target_scope,
                provider,
                collection,
                granted_by,
            )
            granted += status.split()[-1] != "0"
        if dropped:
            status = await conn.execute(
                "DELETE FROM provider_grants WHERE target_scope = $1 AND provider = $2 AND collection = ANY($3::text[])",
                target_scope,
                provider,
                dropped,
            )
            revoked = int(status.split()[-1])
    return {"granted": granted, "revoked": revoked}


async def revoke_provider_grant(
    pool: asyncpg.Pool, *, target_scope: str, provider: str, collection: Optional[str] = None
) -> bool:
    """Remove one grant. A provider-wide revoke (``collection`` None) leaves the collection grants
    alone, and the other way round. True when there was one."""
    status = await pool.execute(
        "DELETE FROM provider_grants WHERE target_scope = $1 AND provider = $2 AND collection IS NOT DISTINCT FROM $3",
        target_scope,
        provider,
        _norm_collection(collection),
    )
    return status.split()[-1] != "0"


async def list_provider_grants(
    pool: asyncpg.Pool,
    *,
    target_scope: Optional[str] = None,
    provider: Optional[str] = None,
    collection: Optional[str] = None,
) -> list[dict]:
    """Grants, each filter optional. ``collection`` matches the collection's own grants AND the
    provider-wide ones that cover it. The admin's view: no access check here."""
    rows = await pool.fetch(
        f"""
        SELECT {_COLUMNS} FROM provider_grants
        WHERE ($1::text IS NULL OR target_scope = $1) AND ($2::text IS NULL OR provider = $2)
          AND ($3::text IS NULL OR collection IS NULL OR collection = $3)
        ORDER BY provider, collection NULLS FIRST, target_scope
        """,
        target_scope,
        provider,
        _norm_collection(collection),
    )
    return [_row(r) for r in rows]


async def grant_access(pool: asyncpg.Pool, *, target_scope: str, provider: str, collection: str) -> GrantAccess:
    """What ``target_scope`` may do with (``provider``, ``collection``): the union of its
    provider-wide grant and its collection grant. One indexed lookup; asked per request."""
    rows = await pool.fetch(
        """
        SELECT attributes, geometry, request FROM provider_grants
        WHERE target_scope = $1 AND provider = $2 AND (collection IS NULL OR collection = $3)
        """,
        target_scope,
        provider,
        _norm_collection(collection),
    )
    return merge_access(dict(r) for r in rows)


async def granted_datasets(
    pool: asyncpg.Pool, *, target_scope: str, permission: str
) -> list[tuple[str, Optional[str]]]:
    """``[(provider, collection | None)]`` granted to ``target_scope`` with ``permission`` -- what a
    search of that scope reads from the provider store (None: every collection of the provider)."""
    if permission not in PERMISSIONS:
        raise ValueError(f"unknown permission {permission!r} (one of {', '.join(PERMISSIONS)})")
    rows = await pool.fetch(
        f"SELECT provider, collection FROM provider_grants WHERE target_scope = $1 AND {permission} "
        "ORDER BY provider, collection NULLS FIRST",
        target_scope,
    )
    return [(r["provider"], r["collection"]) for r in rows]
