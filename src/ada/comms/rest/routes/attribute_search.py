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

NODE BOUNDS live beside the index and are read under the same rule (``GET .../assets/bounds``,
``POST .../assets/bounds/search``; ``db/node_bounds.py``): the scope's own sets, the shared
scope's, and those of the share origins the caller can reach.

A BOUNDS REQUEST (``.../assets/bounds/request``) runs a provider's declared sweep with
``sweep_target: bounds`` over the named collections only -- the viewer's answer to "no source
published node bounds" -- gated as the provider's plugin job is. An ATTRIBUTES REQUEST
(``.../assets/attributes/request``) is the same with ``sweep_target: attributes``.

ONE NODE'S ATTRIBUTES (``GET .../assets/search/node``) are read by node id or full name from the
partitions a search of the scope reads -- what the viewer shows for a selected object.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from ..db.attribute_index import (
    SEARCH_STATEMENT_TIMEOUT,
    SearchQueryError,
    parse_search_query,
)
from ..db.node_bounds import (
    QUERY_STATEMENT_TIMEOUT,
    BoundsQueryError,
    list_bounds_sets,
    parse_bounds_query,
    query_node_bounds,
    readable_scopes,
)
from ..scope import Scope
from .deps import RestContext, require_pool, rest_context, scope_from_path

router = APIRouter()

#: The sweep setting, and its values, that stage a provider's node bounds / attribute index.
BOUNDS_SETTING = "sweep_target"
BOUNDS_TARGET = "bounds"
ATTRIBUTES_TARGET = "attributes"
#: What a request of each target stages, as its permission's ``reason`` words it.
_TARGET_WORDS = {BOUNDS_TARGET: "node bounds", ATTRIBUTES_TARGET: "attribute indexes"}


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
    exclude_partitions, all_scopes, kind, depth, where, name, select, limit, cursor}`` (all optional).

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

    ``name`` matches the node's NAME -- the ``full_name`` column, which no provider need repeat as
    an attribute: ``{"eq": s}``, ``{"prefix": s}`` or ``{"contains": s}``, with ``"ignore_case"``
    (default true for ``contains``, false otherwise) and ``"refs": true`` to match ``source_ref``
    too (either one). A case-sensitive ``eq`` / ``prefix`` of the name alone uses the partition's
    name index. Anything else scans, so it is refused (400) unless the body names ``providers``,
    ``collections`` or ``partitions`` (or carries an ``eq`` / ``in`` attribute filter); it is still
    bounded by the statement timeout, which answers 504. Case folding is ASCII-only.

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
    except asyncpg.QueryCanceledError as exc:
        raise HTTPException(
            status_code=504,
            detail=f"the search did not finish within {SEARCH_STATEMENT_TIMEOUT}; narrow it (collections, kind, "
            "an attribute filter, or a name prefix instead of 'contains')",
        ) from exc
    return JSONResponse(result)


async def _bounds_scopes(request: Request, scope_obj: Scope, user: User) -> tuple[asyncpg.Pool, list[str]]:
    """The pool and the scopes a bounds read of ``scope_obj`` reads for ``user``: its own, the
    shared scope, and the origins of the attribute shares into it the caller may reach -- the same
    origins an attribute search reads (``search_origins``)."""
    pool = require_pool(request)
    origins = await _origins_or_403(pool, scope_obj, user, False)
    return pool, readable_scopes(scope_obj.prefix(), origins.shared_from)


@router.get("/scopes/{scope}/assets/bounds")
async def api_asset_bounds_sets(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Which collections have node bounds readable here (``ada.assets/node-bounds@1``; migration
    042): ``{sets: [{scope, provider, collection, revision, rows, frame, ingested_at}]}``."""
    pool, scopes = await _bounds_scopes(request, scope_obj, user)
    return JSONResponse({"sets": await list_bounds_sets(pool, scopes=scopes)})


@router.post("/scopes/{scope}/assets/bounds/search")
async def api_asset_bounds_search(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """The published nodes in a box, loaded in the viewer or not.

    Body: ``{box: {min: [x, y, z], max: [x, y, z]}, mode: "partial" | "within", collections?,
    providers?, limit?}`` -- the box in the providers' MODEL frame (Z up, the units of their
    geometry; the viewer subtracts its scene offset first). ``partial`` takes every leaf whose box
    meets it, ``within`` every node whose box lies inside it; both answer the topmost nodes that
    stand for whole levels (``db/node_bounds.py``).

    Response: ``{sets, hits: [{scope, provider, collection, node_id, parent_id, subject, label,
    full_name, kind, depth, bbox}], mode, truncated, candidates, took_ms}``. ``sets`` lists the
    bounds sets read with their ``frame``; an empty ``sets`` means no provider published bounds
    for these collections. ``truncated`` says why the answer is incomplete, else null."""
    pool, scopes = await _bounds_scopes(request, scope_obj, user)
    body = await request.json() if await request.body() else {}
    try:
        query = parse_bounds_query(body)
        result = await query_node_bounds(pool, scopes=scopes, query=query)
    except BoundsQueryError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except asyncpg.QueryCanceledError as exc:
        raise HTTPException(
            status_code=504, detail=f"the box query did not finish within {QUERY_STATEMENT_TIMEOUT}; draw a smaller box"
        ) from exc
    return JSONResponse(result)


def sweep_of(specs: list[dict], target: str) -> tuple[dict, dict] | None:
    """``(spec, entry)`` of the first sweep a provider declares that offers ``sweep_target: <target>``."""
    from .asset_schedules import SWEEP, entry_settings, same_choice, schedule_entries

    for spec in specs:
        for entry in schedule_entries(spec):
            if entry.get("kind") != SWEEP:
                continue
            for setting in entry_settings(spec, entry):
                if setting["name"] == BOUNDS_SETTING and any(
                    same_choice(target, c.get("value")) for c in setting.get("choices") or ()
                ):
                    return spec, entry
    return None


def bounds_sweep_of(specs: list[dict]) -> tuple[dict, dict] | None:
    """``(spec, entry)`` of the first sweep a provider declares that offers ``sweep_target: bounds``."""
    return sweep_of(specs, BOUNDS_TARGET)


async def bounds_request_permission(ctx: RestContext, pool, user: User, provider: str) -> dict:
    """``{provider, available, allowed, reason, job, plugin_id}``: whether ``provider`` can be asked
    for node bounds, and by this user -- the plugin job's own admin gate, asked up front."""
    return await sweep_request_permission(ctx, pool, user, provider, BOUNDS_TARGET)


async def sweep_request_permission(ctx: RestContext, pool, user: User, provider: str, target: str) -> dict:
    """:func:`bounds_request_permission` for any ``sweep_target``."""
    from .asset_schedules import provider_specs
    from .plugin_jobs import plugin_job_requires_admin

    found = sweep_of((await provider_specs(ctx)).get(provider) or [], target)
    if found is None:
        return {
            "provider": provider,
            "available": False,
            "allowed": False,
            "reason": f"no online worker of {provider!r} stages {_TARGET_WORDS.get(target, target)}",
            "job": None,
            "plugin_id": None,
        }
    spec, entry = found
    plugin_id = str(spec.get("slug") or spec.get("id") or "")
    allowed, reason = True, None
    if await plugin_job_requires_admin(plugin_id, pool, dict(spec)) and not getattr(user, "is_admin", False):
        allowed, reason = False, f"the {plugin_id!r} job is restricted to administrators"
    return {
        "provider": provider,
        "available": True,
        "allowed": allowed,
        "reason": reason,
        "job": entry["id"],
        "plugin_id": plugin_id,
    }


@router.get("/scopes/{scope}/assets/bounds/request")
async def api_asset_bounds_request_permission(
    request: Request,
    provider: str,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Whether ``provider`` can be asked for node bounds here, and by this user."""
    del scope_obj  # the path's access check is the point
    return JSONResponse(await bounds_request_permission(ctx, require_pool(request), user, provider.strip()))


@router.post("/scopes/{scope}/assets/bounds/request")
async def api_asset_bounds_request(
    scope: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Body ``{provider, collections}``: have ``provider`` measure and publish those collections' node
    bounds -- its declared sweep with ``sweep_target: bounds``, over these collections only, driven
    and recorded like any sweep (``asset_sweeps``). Answers the run (202).

    Refused (403) when the provider's job is restricted to administrators and the user is not one,
    (409) when no online worker of it stages bounds, or while a sweep of the same provider runs."""
    return await _start_target_sweep(request, scope, scope_obj, user, ctx, BOUNDS_TARGET)


async def _start_target_sweep(
    request: Request, scope: str, scope_obj: Scope, user: User, ctx: RestContext, target: str
) -> JSONResponse:
    """Body ``{provider, collections}``: the provider's declared sweep with ``sweep_target: <target>``
    over those collections only (202), refused as :func:`api_asset_bounds_request` says."""
    from .asset_sweeps import SweepRefused, start_sweep

    pool = require_pool(request)
    body = await request.json() if await request.body() else {}
    provider = str((body or {}).get("provider") or "").strip()
    wanted = (body or {}).get("collections")
    if not provider or not isinstance(wanted, list) or not wanted or not all(isinstance(c, str) for c in wanted):
        raise HTTPException(status_code=400, detail="'provider' and a non-empty list of 'collections' are required")
    permission = await sweep_request_permission(ctx, pool, user, provider, target)
    if not permission["available"]:
        raise HTTPException(status_code=409, detail=permission["reason"])
    if not permission["allowed"]:
        raise HTTPException(status_code=403, detail=permission["reason"])
    try:
        run = await start_sweep(
            ctx,
            pool,
            scope=scope,
            scope_obj=scope_obj,
            provider=provider,
            job=permission["job"],
            settings={BOUNDS_SETTING: target},
            user=user,
            via="user",
            collections=wanted,
        )
    except SweepRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JSONResponse(run, status_code=202)


async def _target_sweep_run(ctx: RestContext, pool, scope: str, run_id: str, target: str) -> JSONResponse:
    """A ``sweep_target: <target>`` sweep started from ``scope``, with live progress; 404 otherwise."""
    from .asset_sweeps import sweep_with_progress

    run = await sweep_with_progress(ctx, pool, run_id)
    settings = (run or {}).get("settings") or {}
    if run is None or run.get("scope") != scope or settings.get(BOUNDS_SETTING) != target:
        raise HTTPException(status_code=404, detail=f"no such {target} request in this scope")
    return JSONResponse(run)


@router.get("/scopes/{scope}/assets/bounds/request/{run_id}")
async def api_asset_bounds_request_run(
    scope: str,
    run_id: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """A bounds request started from this scope, with its items' live progress -- what the viewer
    polls. Only a bounds sweep of THIS scope: other sweep reads are an admin's."""
    del scope_obj
    return await _target_sweep_run(ctx, require_pool(request), scope, run_id, BOUNDS_TARGET)


@router.get("/scopes/{scope}/assets/attributes/request")
async def api_asset_attributes_request_permission(
    request: Request,
    provider: str,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Whether ``provider`` can be asked to index attributes here, and by this user."""
    del scope_obj
    return JSONResponse(
        await sweep_request_permission(ctx, require_pool(request), user, provider.strip(), ATTRIBUTES_TARGET)
    )


@router.post("/scopes/{scope}/assets/attributes/request")
async def api_asset_attributes_request(
    scope: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Body ``{provider, collections}``: have ``provider`` stage those collections' attribute index --
    its declared sweep with ``sweep_target: attributes``. Answers and refuses as the bounds request."""
    return await _start_target_sweep(request, scope, scope_obj, user, ctx, ATTRIBUTES_TARGET)


@router.get("/scopes/{scope}/assets/attributes/request/{run_id}")
async def api_asset_attributes_request_run(
    scope: str,
    run_id: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """An attributes request started from this scope, with its items' live progress."""
    del scope_obj
    return await _target_sweep_run(ctx, require_pool(request), scope, run_id, ATTRIBUTES_TARGET)


@router.get("/scopes/{scope}/assets/search/node")
async def api_asset_search_node(
    request: Request,
    provider: str,
    collection: str,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    node_id: str | None = None,
    name: str | None = None,
) -> JSONResponse:
    """One node's indexed attributes, by ``node_id`` or by ``name`` (its full name), exactly one.

    Response ``{indexed, node, ambiguous}``: ``indexed`` false when no partition of (provider,
    collection) is readable here; ``node`` null when none matches, or (``ambiguous``) when the name
    names several. Never a 404: absence is an answer."""
    if bool(node_id) == bool(name):
        raise HTTPException(status_code=400, detail="exactly one of 'node_id' and 'name' is required")
    pool = require_pool(request)
    origins = await _origins_or_403(pool, scope_obj, user, False)
    try:
        result = await db_module.node_attributes(
            pool,
            scope=scope_obj.prefix(),
            provider=provider,
            collection=collection,
            node_id=node_id or None,
            full_name=name or None,
            shared_from=origins.shared_from,
            viewer=user.sub,
        )
    except asyncpg.QueryCanceledError as exc:
        raise HTTPException(status_code=504, detail=f"the lookup did not finish within {SEARCH_STATEMENT_TIMEOUT}") from exc
    return JSONResponse(result)


@router.post("/scopes/{scope}/assets/search/nodes")
async def api_asset_search_nodes(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Many nodes of one collection at once. Body ``{collection, node_ids, full_names, keys,
    ancestors, providers}``: ``keys`` restricts ``attrs``, ``ancestors`` adds the rows up to that
    many levels above the matches. At most ``NODES_LOOKUP_MAX`` ids and names per request.

    Response ``{indexed, nodes, ancestors}`` (``db.attribute_index.nodes_attributes``)."""
    from ..db.attribute_index import NODES_LOOKUP_MAX, nodes_attributes

    body = await request.json()
    if not isinstance(body, dict) or not isinstance(body.get("collection"), str) or not body["collection"].strip():
        raise HTTPException(status_code=400, detail="'collection' is required")

    def strings(name: str) -> list[str] | None:
        value = body.get(name)
        if value is None:
            return None
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise HTTPException(status_code=400, detail=f"'{name}' must be a list of strings")
        return value

    ids, names = strings("node_ids") or [], strings("full_names") or []
    if len(ids) + len(names) > NODES_LOOKUP_MAX:
        raise HTTPException(
            status_code=400, detail=f"at most {NODES_LOOKUP_MAX} node ids and names per request; send it in chunks"
        )
    ancestors = body.get("ancestors", 0)
    if not isinstance(ancestors, int) or ancestors < 0:
        raise HTTPException(status_code=400, detail="'ancestors' must be a non-negative integer")
    pool = require_pool(request)
    origins = await _origins_or_403(pool, scope_obj, user, False)
    try:
        result = await nodes_attributes(
            pool,
            scope=scope_obj.prefix(),
            collection=body["collection"].strip(),
            node_ids=ids,
            full_names=names,
            keys=strings("keys"),
            ancestors=ancestors,
            providers=strings("providers"),
            shared_from=origins.shared_from,
        )
    except asyncpg.QueryCanceledError as exc:
        raise HTTPException(
            status_code=504, detail=f"the lookup did not finish within {SEARCH_STATEMENT_TIMEOUT}"
        ) from exc
    return JSONResponse(result)


@router.post("/scopes/{scope}/assets/relations")
async def api_asset_relations(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """The relations FROM many nodes of one collection. Body ``{collection, node_ids, full_names}``,
    at most ``LOOKUP_MAX`` ids and names. Response ``{indexed, relations, token}``
    (``db.node_relations.relations_for``)."""
    from ..db.node_relations import LOOKUP_MAX, readable_scopes, relations_for

    body = await request.json()
    if not isinstance(body, dict) or not isinstance(body.get("collection"), str) or not body["collection"].strip():
        raise HTTPException(status_code=400, detail="'collection' is required")
    lists = []
    for name in ("node_ids", "full_names"):
        value = body.get(name) or []
        if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
            raise HTTPException(status_code=400, detail=f"'{name}' must be a list of strings")
        lists.append(value)
    if sum(len(v) for v in lists) > LOOKUP_MAX:
        raise HTTPException(status_code=400, detail=f"at most {LOOKUP_MAX} node ids and names per request")
    pool = require_pool(request)
    origins = await _origins_or_403(pool, scope_obj, user, False)
    try:
        result = await relations_for(
            pool,
            scopes=readable_scopes(scope_obj.prefix(), origins.shared_from or ()),
            collection=body["collection"].strip(),
            node_ids=lists[0],
            full_names=lists[1],
        )
    except asyncpg.QueryCanceledError as exc:
        raise HTTPException(status_code=504, detail="the relations lookup did not finish in time") from exc
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
