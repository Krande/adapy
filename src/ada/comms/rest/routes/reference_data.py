"""What a scope's collections offer for a new element to point at (``db/reference_data.py``,
migration 049): every spec, spec component and material a provider listed for a collection, used by
the model or not -- the SPRE / MATR values a macro may write.

``GET /scopes/{scope}/assets/reference`` lists the sets readable here; ``GET .../assets/reference/items``
pages through one set's entries, filtered, with how many elements use each one (``usage=true``).

READ LIKE THE ATTRIBUTE INDEX. A scope reads its own sets, the shared scope's and those of the share
origins the caller can reach (``attribute_search.search_origins``), and -- through an ``attributes``
grant (045) -- the provider store's set of a granted (provider, collection), which then shadows the
scope's own copy of the same. The provider store is never a path a member names; its rows say
``scope: "providers"``.
"""

from __future__ import annotations

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import auth as auth_module
from ..auth import User
from ..db.reference_data import (
    QUERY_STATEMENT_TIMEOUT,
    ReferenceQueryError,
    list_reference_sets,
    parse_reference_query,
    query_reference_items,
)
from ..scope import Scope
from .attribute_search import _origins_or_403
from .deps import require_pool, scope_from_path
from .provider_store import PROVIDER_STORE

router = APIRouter()


async def _readable_sets(request: Request, scope_obj: Scope, user: User) -> tuple[asyncpg.Pool, list[dict]]:
    from ..db.node_bounds import readable_scopes

    pool = require_pool(request)
    origins = await _origins_or_403(pool, scope_obj, user, False)
    sets = await list_reference_sets(
        pool,
        scopes=readable_scopes(scope_obj.prefix(), origins.shared_from),
        store=PROVIDER_STORE.prefix(),
        granted=origins.granted,
    )
    return pool, sets


@router.get("/scopes/{scope}/assets/reference")
async def api_reference_sets(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """``{sets: [{scope, provider, collection, revision, rows, counts: {kind: n}, ingested_at}]}``:
    the collections whose reference data is readable here."""
    _, sets = await _readable_sets(request, scope_obj, user)
    return JSONResponse({"sets": sets})


@router.get("/scopes/{scope}/assets/reference/items")
async def api_reference_items(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """One page of a set's entries. Query: ``provider``, ``collection`` (required); ``kind``
    (``spec`` / ``spco`` / ``material``), ``use`` (``section``, ``plate``, ``fitting``, ``joint``,
    ``material``, ``other``), ``owner`` (an SPCO's spec), ``prefix`` (of ``ref``, case-sensitive),
    ``q`` (in ``ref``, name or attributes, any case), ``limit`` (default 200, at most 1000),
    ``offset``, ``usage`` (``true``: count each entry's users in the collection's attribute index).

    Response: ``{set, items: [{kind, ref, name, owner, use, attrs, used?}], total, facets: {kinds,
    uses}, next_offset, usage, took_ms}``. 404 when no set of that (provider, collection) is
    readable here."""
    try:
        query = parse_reference_query(dict(request.query_params))
    except ReferenceQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    pool, sets = await _readable_sets(request, scope_obj, user)
    held = next((s for s in sets if s["provider"] == query.provider and s["collection"] == query.collection), None)
    if held is None:
        raise HTTPException(
            status_code=404,
            detail=f"no reference data of {query.provider}/{query.collection} is readable here -- "
            "an admin runs the provider's export with its catalogue & reference data ticked, and grants it",
        )
    try:
        result = await query_reference_items(pool, scope=held["scope"], query=query)
    except asyncpg.QueryCanceledError as exc:
        raise HTTPException(
            status_code=504, detail=f"the query did not finish within {QUERY_STATEMENT_TIMEOUT}; narrow it"
        ) from exc
    return JSONResponse(result)
