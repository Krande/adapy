"""Attribute search across every collection a scope's providers have indexed.

``POST /scopes/{scope}/assets/search`` answers "which nodes have these attributes" from the
attribute index the publish job ingests (``ada.assets/attribute-index@1``; migration 037,
``db/attribute_index.py``). It is provider-neutral on purpose: the viewer's search panel and the
export job ask one question of one API, whichever provider published the rows, and a hit names the
provider, collection and node id the published tree uses -- so it can be revealed there.

The scope's own partitions are read, and the partitions an admin shared into it from another
scope (migration 039) -- but a shared one only while the caller can still reach the scope it was
shared FROM. The scope comes from the path, through the same access check every
``/scopes/{scope}/assets/...`` route uses; the shares readable to this caller are resolved once
per request (:func:`search_origins`) and handed to the database layer, which reads nothing else.
Every row and collection says its origin (``scope``, ``scope_wire``, ``shared``).

AN ADMIN MAY SEARCH EVERY PROVIDER SCOPE (``all_scopes``, a body field of the search and a query
parameter of the listings): the shared scope, every live project and the corpora, shared here or
not (``admin_search_origins``) -- the same origin-tagged rows a share gives, each partition read
that way saying ``admin``. Refused (403) for anyone else. Another user's personal scope is never
among them.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from ..db.attribute_index import SearchQueryError, parse_search_query
from ..scope import Scope
from .deps import require_pool, scope_from_path

router = APIRouter()


@dataclass(frozen=True)
class SearchOrigins:
    """The other scopes a search of one scope reads, for one caller: ``shared_from`` (shares into it
    whose origin the caller can reach) and ``read_from`` (an admin's ``all_scopes``)."""

    shared_from: tuple[str, ...] = ()
    read_from: tuple[str, ...] = ()

    def readable(self, own: str) -> frozenset[str]:
        """Every origin a hit of such a search may come from -- what an export may read."""
        return frozenset({own, *self.shared_from, *self.read_from})


class AllScopesRefused(PermissionError):
    """``all_scopes`` asked for by someone who is not an admin."""


async def search_origins(pool, scope_obj: Scope, user: User, *, all_scopes: bool = False) -> SearchOrigins:
    """The origins a search of ``scope_obj`` by ``user`` reads (``readable_share_origins``, plus
    ``admin_search_origins`` for an admin's ``all_scopes``). Raises :class:`AllScopesRefused` for a
    non-admin's ``all_scopes``."""
    is_admin = bool(getattr(user, "is_admin", False))
    if all_scopes and not is_admin:
        raise AllScopesRefused("searching every scope ('all_scopes') is for administrators")
    if pool is None:  # no database: no index, so no other scope's either
        return SearchOrigins()
    shared = await db_module.readable_share_origins(
        pool, scope=scope_obj.prefix(), user_sub=getattr(user, "sub", ""), is_admin=is_admin
    )
    whole = await db_module.admin_search_origins(pool, scope=scope_obj.prefix()) if all_scopes else []
    return SearchOrigins(tuple(shared), tuple(whole))


async def _origins_or_403(pool, scope_obj: Scope, user: User, all_scopes: bool) -> SearchOrigins:
    try:
        return await search_origins(pool, scope_obj, user, all_scopes=all_scopes)
    except AllScopesRefused as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.post("/scopes/{scope}/assets/search")
async def api_asset_search(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Body: ``{providers, collections, exclude_providers, exclude_collections, partitions,
    exclude_partitions, all_scopes, kind, depth, where, select, limit, cursor}`` (all optional).

    ``providers`` / ``collections`` include (``null``: every one); ``exclude_providers`` /
    ``exclude_collections`` leave those out whatever the includes say, and are never read.
    Collections compare case-insensitively. They apply to shared partitions as to own ones -- by
    NAME, so they cannot tell two scopes' partitions of one collection apart. ``partitions`` /
    ``exclude_partitions`` can: they name partitions by their ``counts`` key (``key`` in the
    collections listing).

    ``all_scopes: true`` (admins only, 403 otherwise) also reads every provider scope's partitions,
    shared here or not (see the module docstring).

    ``where`` items are ``{"key", "eq"}``, ``{"key", "in": [...]}``, ``{"key", "prefix"}``,
    ``{"key", "ne"}``, ``{"key", "not_in": [...]}``, ``{"key", "exists": true}`` or
    ``{"key", "missing": true}``; keys are case-sensitive, as the source writes them. ``eq`` / ``in``
    match a number and its text spelling alike (``3`` and ``"3"``); ``ne`` / ``not_in`` are their exact
    complement -- they also match nodes that do not have the key at all (add ``exists`` to rule
    those out). The negative operators and ``exists`` / ``missing`` cannot use the attribute index:
    beside a positive filter they refine its matches cheaply, but a query of ONLY such filters scans
    every chosen collection and may hit the search's statement timeout (a 30 s ceiling) on large
    scopes -- narrow it with a positive filter, a kind or the collections.

    Response: ``{rows, next_cursor, counts, partitions, took_ms}`` where ``counts`` is the whole
    query's match count per partition -- keyed ``<provider>/<collection>`` for the scope's own,
    ``<provider>/<collection>@<origin scope>`` for another scope's -- and ``partitions`` lists the
    same with those parts and the origin spelled out. Each row carries its ancestors (``path``,
    root first) and its origin: ``scope`` (the storage prefix), ``scope_wire`` (the path segment
    that names it to this caller, or null), ``shared`` and -- read through ``all_scopes`` alone --
    ``admin``. A ``cursor`` replayed with different filters (exclusions included) is refused (400)."""
    pool = require_pool(request)
    body = await request.json() if await request.body() else {}
    try:
        query = parse_search_query(body)
        origins = await _origins_or_403(pool, scope_obj, user, query.all_scopes)
        result = await db_module.search_attributes(
            pool,
            scope=scope_obj.prefix(),
            query=query,
            shared_from=origins.shared_from,
            read_from=origins.read_from,
            viewer=user.sub,
        )
    except SearchQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(result)


@router.get("/scopes/{scope}/assets/search/collections")
async def api_asset_search_collections(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    all_scopes: bool = False,
) -> JSONResponse:
    """What is searchable in this scope: one entry per indexed provider collection, its own and
    those shared into it (with ``all_scopes``, an admin's: every provider scope's), each with its
    origin (``scope``, ``scope_wire``, ``shared``) and ``key``, the name a search filters it by."""
    pool = require_pool(request)
    origins = await _origins_or_403(pool, scope_obj, user, all_scopes)
    collections = await db_module.list_attr_collections(
        pool,
        scope=scope_obj.prefix(),
        shared_from=origins.shared_from,
        read_from=origins.read_from,
        viewer=user.sub,
    )
    return JSONResponse({"collections": collections})


@router.get("/scopes/{scope}/assets/search/keys")
async def api_asset_search_keys(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    provider: str | None = None,
    collection: str | None = None,
    all_scopes: bool = False,
) -> JSONResponse:
    """Every attribute key in the scope's index (or one provider's / one collection's), sorted."""
    pool = require_pool(request)
    origins = await _origins_or_403(pool, scope_obj, user, all_scopes)
    keys = await db_module.list_attr_keys(
        pool,
        scope=scope_obj.prefix(),
        provider=provider or None,
        collection=collection or None,
        shared_from=origins.shared_from,
        read_from=origins.read_from,
    )
    return JSONResponse({"keys": keys})


@router.get("/scopes/{scope}/assets/search/values")
async def api_asset_search_values(
    request: Request,
    key: str,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    prefix: str | None = None,
    limit: int = 50,
    provider: str | None = None,
    collection: str | None = None,
    all_scopes: bool = False,
) -> JSONResponse:
    """The commonest values of one key (as text), optionally starting with ``prefix``."""
    pool = require_pool(request)
    if not key:
        raise HTTPException(status_code=400, detail="'key' is required")
    origins = await _origins_or_403(pool, scope_obj, user, all_scopes)
    values = await db_module.list_attr_values(
        pool,
        scope=scope_obj.prefix(),
        key=key,
        prefix=prefix or None,
        limit=limit,
        provider=provider or None,
        collection=collection or None,
        shared_from=origins.shared_from,
        read_from=origins.read_from,
    )
    return JSONResponse({"values": values})
