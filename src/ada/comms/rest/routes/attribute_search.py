"""Attribute search across every collection a scope's providers have indexed.

``POST /scopes/{scope}/assets/search`` answers "which nodes have these attributes" from the
attribute index the publish job ingests (``ada.assets/attribute-index@1``; migration 037,
``db/attribute_index.py``). It is provider-neutral on purpose: the viewer's search panel and the
export job ask one question of one API, whichever provider published the rows, and a hit names the
provider, collection and node id the published tree uses -- so it can be revealed there.

Only the scope's own partitions are ever read: the scope comes from the path, through the same
access check every ``/scopes/{scope}/assets/...`` route uses, and the database layer looks
partitions up by it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import db as db_module
from ..db.attribute_index import SearchQueryError, parse_search_query
from ..scope import Scope
from .deps import require_pool, scope_from_path

router = APIRouter()


@router.post("/scopes/{scope}/assets/search")
async def api_asset_search(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    """Body: ``{providers, collections, kind, depth, where, select, limit, cursor}`` (all optional).

    ``where`` items are ``{"key", "eq"}``, ``{"key", "in": [...]}`` or ``{"key", "prefix"}``; keys are
    case-sensitive, as the source writes them. Response: ``{rows, next_cursor, counts, took_ms}``
    where ``counts`` is the whole query's match count per ``<provider>/<collection>`` and each row
    carries its ancestors (``path``, root first)."""
    pool = require_pool(request)
    body = await request.json() if await request.body() else {}
    try:
        query = parse_search_query(body)
        result = await db_module.search_attributes(pool, scope=scope_obj.prefix(), query=query)
    except SearchQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(result)


@router.get("/scopes/{scope}/assets/search/collections")
async def api_asset_search_collections(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
) -> JSONResponse:
    """What is searchable in this scope: one entry per indexed provider collection."""
    pool = require_pool(request)
    return JSONResponse({"collections": await db_module.list_attr_collections(pool, scope=scope_obj.prefix())})


@router.get("/scopes/{scope}/assets/search/keys")
async def api_asset_search_keys(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    provider: str | None = None,
    collection: str | None = None,
) -> JSONResponse:
    """Every attribute key in the scope's index (or one provider's / one collection's), sorted."""
    pool = require_pool(request)
    keys = await db_module.list_attr_keys(
        pool, scope=scope_obj.prefix(), provider=provider or None, collection=collection or None
    )
    return JSONResponse({"keys": keys})


@router.get("/scopes/{scope}/assets/search/values")
async def api_asset_search_values(
    request: Request,
    key: str,
    scope_obj: Scope = Depends(scope_from_path),
    prefix: str | None = None,
    limit: int = 50,
    provider: str | None = None,
    collection: str | None = None,
) -> JSONResponse:
    """The commonest values of one key (as text), optionally starting with ``prefix``."""
    pool = require_pool(request)
    if not key:
        raise HTTPException(status_code=400, detail="'key' is required")
    values = await db_module.list_attr_values(
        pool,
        scope=scope_obj.prefix(),
        key=key,
        prefix=prefix or None,
        limit=limit,
        provider=provider or None,
        collection=collection or None,
    )
    return JSONResponse({"values": values})
