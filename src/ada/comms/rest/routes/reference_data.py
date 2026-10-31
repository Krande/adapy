"""What a scope's collections offer for a new element to point at (``db/reference_data.py``,
migration 049): every spec, spec component and material a provider listed for a collection, used by
the model or not -- the SPRE / MATR values a macro may write.

``GET /scopes/{scope}/assets/reference`` lists the sets readable here; ``GET .../assets/reference/items``
pages through one set's entries, filtered, with how many elements use each one (``usage=true``).

THE MODEL-BUILDING CATALOGUE (``GET /scopes/{scope}/catalogue/sections`` and ``.../materials``,
``db/catalogue.py``) is the same reference data, provider-agnostic: the sections and materials a
scope may build with, merged across its readable collections, in adapy's own parameter names and
with an admin's material overrides merged in -- what ``ada.catalogue`` reads from a script. Admins
set the overrides through ``/api/admin/reference/overrides``.

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


# --- the model-building catalogue ---------------------------------------------------------------


async def _catalogue(request: Request, scope_obj: Scope, user: User, what: str) -> JSONResponse:
    from ..db.catalogue import CatalogueQueryError, parse_catalogue_query, query_catalogue

    try:
        query = parse_catalogue_query(dict(request.query_params))
    except CatalogueQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    pool, sets = await _readable_sets(request, scope_obj, user)
    return JSONResponse(await query_catalogue(pool, sets=sets, what=what, query=query))


@router.get("/scopes/{scope}/catalogue/sections")
async def api_catalogue_sections(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """The sections (beam profiles and plate thicknesses) this scope may build with: every section /
    plate spec component of the reference data readable here, merged across collections. Query:
    ``collection``, ``provider``, ``type`` (an adapy section type: I, BOX, TUB, T, HP, UNP, CIRC, FB,
    or PLATE), ``use`` (section / plate), ``ref`` (exact), ``q``, ``limit`` (default 1000, at most
    5000), ``offset``.

    ``{items: [{ref, name, collection, provider, use, spec, section, gtype, params, plines}], total,
    next_offset, collections}``. ``section`` is ``{type, dims, units, gtype?, source?}`` in adapy's
    parameter names, or null where the provider could not say; ``ref`` is the exact reference a
    model element writes."""
    return await _catalogue(request, scope_obj, user, "sections")


@router.get("/scopes/{scope}/catalogue/materials")
async def api_catalogue_materials(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """The materials this scope may build with, merged across collections, each with the provider's
    properties, an admin's override and the two merged (``material.effective``). Query as for
    sections, less ``type`` / ``use``."""
    return await _catalogue(request, scope_obj, user, "materials")


# --- overrides (admin) --------------------------------------------------------------------------

admin_router = APIRouter()


async def _body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="the body must be JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="the body must be a JSON object")
    return body


@admin_router.get("/reference/overrides")
async def admin_reference_overrides(
    request: Request, provider: str | None = None, collection: str | None = None
) -> JSONResponse:
    """``{overrides: [{provider, collection, kind, ref, props, note, updated_by, updated_at,
    orphaned}]}`` -- ``orphaned``: no reference set lists the entry any more."""
    from ..db.catalogue import list_overrides

    pool = require_pool(request)
    return JSONResponse(
        {"overrides": await list_overrides(pool, provider=provider or None, collection=collection or None)}
    )


@admin_router.put("/reference/overrides")
async def admin_reference_override_put(
    request: Request, user: User = Depends(auth_module.current_user)
) -> JSONResponse:
    """Body ``{provider, collection, kind, ref, props, note?}``: set (replace) an entry's override.
    A material takes ``family``, ``grade``, ``E``, ``v``, ``rho``, ``sig_y``, ``sig_u``, ``alpha``,
    ``zeta`` (SI units); a ``null`` value leaves the provider's showing."""
    from ..db.catalogue import OverrideError, parse_override, put_override

    pool = require_pool(request)
    try:
        override = parse_override(await _body(request))
    except OverrideError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"override": await put_override(pool, override=override, updated_by=user.sub)})


@admin_router.delete("/reference/overrides")
async def admin_reference_override_delete(request: Request) -> JSONResponse:
    """Body ``{provider, collection, kind, ref}``: remove an entry's override."""
    from ..db.catalogue import delete_override

    pool = require_pool(request)
    body = await _body(request)
    keys = [body.get(k) for k in ("provider", "collection", "kind", "ref")]
    if not all(isinstance(k, str) and k.strip() for k in keys):
        raise HTTPException(status_code=400, detail="'provider', 'collection', 'kind' and 'ref' are required")
    provider, collection, kind, ref = keys
    removed = await delete_override(pool, provider=provider.strip(), collection=collection, kind=kind.strip(), ref=ref)
    return JSONResponse({"removed": removed})
