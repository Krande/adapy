"""Attribute-search results exported as IFC files into a folder the user chooses -- and the
per-file provenance any file can be asked for.

``POST /scopes/{scope}/assets/search/export`` takes the search (``query``) and either the hits the
user selected or none (= every match of the query, paged through here), checks that the user may
write to the target scope, assigns every hit its file key, and starts a RUN: one item per
(provider, collection) batch, each one ``export_search_asset`` job (``formats/export_search.py``).

CONCURRENT, BOUNDED. Unlike a provider sweep, an export holds no licensed seat -- it reads published
blobs -- so up to :data:`EXPORT_CONCURRENCY` items are in flight at once. Each item is timed on
its own (``queued_ms`` / ``run_ms`` seen from here, the job's phases from its summary), and a hit
without geometry or a file that failed is that FILE's outcome, not the run's.

THE DRIVER is the sweeps' (``run_driver.py``): a task in this API process, heartbeat on every poll,
``interrupted`` when it is gone, cancel (the whole run) and skip (one item, the run goes on).

``<folder>/_export.json`` (``ada.files/export@1``) lists the files in export order -- batch by
batch, hit by hit -- and is rewritten as each item finishes, so the gallery can walk a folder in the
order the search returned it.

MISSING GEOMETRY IS ASKED ABOUT, NOT ASSUMED. ``POST .../export/preflight`` resolves every hit
against the geometry provider's published content and writes nothing; the dialog shows what lacks
geometry and asks whether to queue a request for it first. ``request_missing_geometry: true`` (the
answer "yes", default false) makes each item ask the provider for its missing nodes and publish
the answers BEFORE its export job is submitted -- here, in the driver, because that is
orchestration (the API's jobs) and the driver already polls jobs. One item's request phase at a
time (``request_gate``), so a run's requests do not interleave projects on a shared seat. See
``routes/geometry_requests.py``.

``GET /scopes/{scope}/files/meta?key=`` answers ``ada.files/meta@1`` for ANY file: the export's
sidecar when there is one, else the provenance the file carries itself (an IFC's ``ADA_*`` property
sets, a GLB's ``asset.extras.provenance``), else 404.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from ..db.attribute_index import SearchQueryError, parse_search_query
from ..job_transport import JobRequest
from ..run_driver import (
    ItemSkipped,
    RunCancelled,
    json_of,
    ms,
    spawn,
    user_label,
    wait_for_job,
)
from ..scope import Scope
from ..search_export import (
    EXPORT_INDEX_FILENAME,
    EXPORT_INDEX_SCHEMA,
    EXPORT_SEARCH_KIND,
    GEOMETRY_DESIGN,
    GEOMETRY_KINDS,
    GEOMETRY_MESH,
    GEOMETRY_PROVIDER,
    META_SCHEMA,
    clean_folder,
    export_file_key,
    export_merged_key,
    meta_document,
    meta_sidecar_key,
    sidecar_key,
    thumb_sidecar_key,
)
from . import geometry_requests as greq
from .deps import (
    RestContext,
    parse_scope,
    require_pool,
    resolve_project_scope,
    rest_context,
    scope_from_path,
)

router = APIRouter()

POLL_INTERVAL_S = 3.0
#: Items in flight at once. An export is I/O and CPU on the workers, not a licensed seat.
EXPORT_CONCURRENCY = 4
#: One batch's job: a few hundred IFC files is minutes, not hours.
ITEM_TIMEOUT_S = 60 * 60
#: Most hits one export may write. More is a folder nobody browses -- narrow the search.
MAX_EXPORT_HITS = 2000
#: Most hits a MERGED export may take. The folder argument above is gone -- it is one file per
#: collection -- so what bounds it is the work: every hit is still read (or built) one at a time,
#: inside its batch's ITEM_TIMEOUT_S, and a collection's file holds all of its hits at once.
MAX_MERGED_EXPORT_HITS = 10000
#: Largest in-file provenance read the meta route does (bytes).
META_FALLBACK_MAX_BYTES = 128 * 1024 * 1024
DERIVED_FORMATS = ("glb", "step")


class ExportRefused(Exception):
    """The export cannot start, with the reason (a 4xx)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


# --- the request ----------------------------------------------------------------------------------


def _clean_path(raw: Any) -> list[dict]:
    out = []
    for p in raw or ():
        if isinstance(p, dict) and p.get("node_id"):
            out.append(
                {
                    "node_id": str(p["node_id"]),
                    "full_name": p.get("full_name"),
                    "kind": p.get("kind"),
                    "depth": p.get("depth"),
                }
            )
    return out


def clean_hit(raw: Any, where_keys: list[str]) -> dict | None:
    """A hit as the job receives it: identity, ancestors and the matched attributes only (the job
    re-reads full attributes from the index)."""
    if not isinstance(raw, dict):
        return None
    provider, collection, node_id = raw.get("provider"), raw.get("collection"), raw.get("node_id")
    if not (isinstance(provider, str) and provider and isinstance(collection, str) and collection and node_id):
        return None
    attrs = raw.get("attrs") if isinstance(raw.get("attrs"), dict) else {}
    return {
        "provider": provider,
        "collection": collection.lower(),
        "node_id": str(node_id),
        "full_name": raw.get("full_name"),
        "source_ref": raw.get("source_ref"),
        "kind": raw.get("kind"),
        "depth": raw.get("depth"),
        "path": _clean_path(raw.get("path")),
        "attrs": {k: attrs[k] for k in where_keys if k in attrs},
    }


def batch_hits(
    hits: list[dict], folder: str, ext: str = "ifc", *, merge: bool = False
) -> list[tuple[str, str, list[dict]]]:
    """``[(provider, collection, hits)]`` in first-seen order, every hit given its file key. A node
    named twice (selected twice, or by two pages) is written once. A hit that already names its
    ``file`` (a design file requested for a mesh one, which takes its stem) keeps it.

    ``merge``: every hit of a batch gets the batch's ONE file, ``<folder>/<collection>.<ext>``
    (:func:`export_merged_key`) -- with the provider appended when two providers' batches share a
    collection name, so neither overwrites the other."""
    taken: set[str] = {h["file"] for h in hits if h.get("file")}
    seen: set[tuple[str, str, str]] = set()
    batches: dict[tuple[str, str], list[dict]] = {}
    merged_keys: dict[tuple[str, str], str] = {}
    for hit in hits:
        ident = (hit["provider"], hit["collection"], hit["node_id"])
        if ident in seen:
            continue
        seen.add(ident)
        batch = (hit["provider"], hit["collection"])
        if merge:
            if batch not in merged_keys:
                key = export_merged_key(folder, hit["collection"], ext)
                if key in taken:
                    key = export_merged_key(folder, f"{hit['collection']}__{hit['provider']}", ext)
                taken.add(key)
                merged_keys[batch] = key
            hit["file"] = merged_keys[batch]
        elif not hit.get("file"):
            hit["file"] = export_file_key(
                folder, hit["collection"], hit.get("full_name"), hit["node_id"], taken, ext=ext
            )
        batches.setdefault(batch, []).append(hit)
    return [(p, c, h) for (p, c), h in batches.items()]


async def all_hits(pool, *, scope: str, query_body: dict, limit: int = MAX_EXPORT_HITS) -> list[dict]:
    """Every row of the query, paged through the search itself. Refuses (409) past ``limit``."""
    body = {k: v for k, v in (query_body or {}).items() if k not in ("cursor", "limit", "select")}
    body["limit"] = 1000
    rows: list[dict] = []
    cursor = None
    while True:
        try:
            q = parse_search_query({**body, "cursor": cursor})
        except SearchQueryError as exc:
            raise ExportRefused(400, f"the query: {exc}") from exc
        page = await db_module.search_attributes(pool, scope=scope, query=q)
        total = sum(int(n) for n in (page.get("counts") or {}).values())
        if total > limit:
            raise ExportRefused(
                409, f"the query matches {total} nodes; an export writes at most {limit} files -- narrow the search"
            )
        rows.extend(page["rows"])
        cursor = page.get("next_cursor")
        if not cursor:
            return rows


async def _writable_scope(pool, raw: str, user: User) -> Scope:
    """The target scope, checked the way an upload is: the user must be able to reach it."""
    from ..scope import can_access

    if not isinstance(raw, str) or not raw.strip():
        raise ExportRefused(400, "'target_scope' is required")
    target = await resolve_project_scope(pool, parse_scope(raw.strip(), user))
    if not await can_access(user, target, pool):
        raise ExportRefused(403, f"you cannot write to scope {raw!r}")
    return target


def _scope_ref(scope: Scope) -> dict:
    return {"kind": scope.kind, "id": scope.id}


def export_job_opts(run_id: str, options: dict, *, scope: str, target: Scope, folder: str, query: Any, user) -> dict:
    """What every item's job is given, from the run's stored ``options``."""
    return {
        "run_id": run_id,
        "geometry": options.get("geometry") or GEOMETRY_DESIGN,
        "merge": bool(options.get("merge")),
        "request_trees": bool(options.get("request_trees")),
        "write_index": True,
        "source_scope": scope,
        "target_scope": _scope_ref(target),
        "target_folder": folder,
        "query": query,
        "include_source_attributes": options.get("include_source_attributes", True) is not False,
        "also": list(options.get("also") or []),
        "requested_by": user_label(user),
        "request_missing_geometry": bool(options.get("request_missing_geometry")),
    }


@router.post("/scopes/{scope}/assets/search/export")
async def api_search_export(
    scope: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Body: ``{query, hits|null, target_scope, target_folder, include_source_attributes, also}``.
    Answers the run (202)."""
    pool = require_pool(request)
    ctx.jobs.require("export_search")
    body = await request.json() if await request.body() else {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="the body must be a JSON object")
    try:
        run = await start_export(ctx, pool, scope=scope, scope_obj=scope_obj, body=body, user=user)
    except ExportRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return JSONResponse(_as_wire(run, scope), status_code=202)


async def start_export(
    ctx,
    pool,
    *,
    scope: str,
    scope_obj: Scope,
    body: dict,
    user,
    preset_files: dict[str, tuple[str, str | None]] | None = None,
) -> dict:
    """``preset_files``: ``{node_id: (file, mesh_file)}`` for hits whose file key is already decided
    (:func:`api_file_request_geometry`); every other hit gets one from :func:`batch_hits`."""
    query = body.get("query") if isinstance(body.get("query"), dict) else {}
    try:
        parsed = parse_search_query({k: v for k, v in query.items() if k not in ("cursor", "limit")})
    except SearchQueryError as exc:
        raise ExportRefused(400, f"the query: {exc}") from exc
    where_keys = [w["key"] for w in parsed.where]
    try:
        folder = clean_folder(str(body.get("target_folder") or ""))
    except ValueError as exc:
        raise ExportRefused(400, str(exc)) from exc
    target = await _writable_scope(pool, body.get("target_scope"), user)
    geometry = str(body.get("geometry") or GEOMETRY_DESIGN)
    if geometry not in GEOMETRY_KINDS:
        raise ExportRefused(400, f"'geometry' must be one of {list(GEOMETRY_KINDS)}")
    mesh = geometry == GEOMETRY_MESH
    also = body.get("also") or []
    if not isinstance(also, list) or not set(also) <= set(DERIVED_FORMATS):
        raise ExportRefused(400, f"'also' must be a subset of {list(DERIVED_FORMATS)}")
    include_attrs = body.get("include_source_attributes", True) is not False
    request_missing = body.get("request_missing_geometry") is True
    merge = body.get("merge") is True
    # A mesh export's "request trees, then export": a collection with no published tree asks its
    # provider for one first (`request_collection_tree`), inside the run.
    request_trees = body.get("request_trees") is True
    if request_trees and not mesh:
        raise ExportRefused(
            400, "'request_trees' is for mesh exports; a design export asks with 'request_missing_geometry'"
        )
    if merge and preset_files:
        # A preset file is ONE node's file (a design request for a mesh file); a merge has none.
        raise ExportRefused(400, "a merged export writes one file per collection; it takes no preset files")
    cap = MAX_MERGED_EXPORT_HITS if merge else MAX_EXPORT_HITS
    if mesh:
        # A mesh export IS the GLB, built from what the hit's provider published: nothing to convert
        # and nothing to request. Said rather than ignored, so a client sending both learns why.
        if also or request_missing:
            raise ExportRefused(
                400, "a mesh export writes GLBs only: 'also' and 'request_missing_geometry' are for design exports"
            )
        ctx.jobs.require("asset_build")
    if request_missing:
        req = await greq.geometry_node_request(ctx)
        allowed, why = await request_permission(pool, user, req)
        if not allowed:
            raise ExportRefused(403 if req is not None else 409, f"missing geometry cannot be requested: {why}")

    raw_hits = body.get("hits")
    if raw_hits is None:
        raw_hits = await all_hits(pool, scope=scope_obj.prefix(), query_body=query, limit=cap)
    elif not isinstance(raw_hits, list):
        raise ExportRefused(400, "'hits' must be a list of search rows, or null for every match")
    if len(raw_hits) > cap:
        what = "a merged export takes" if merge else "an export writes"
        raise ExportRefused(409, f"{len(raw_hits)} hits; {what} at most {cap}")
    hits = [h for h in (clean_hit(r, where_keys) for r in raw_hits) if h is not None]
    if not hits:
        raise ExportRefused(400, "nothing to export: no hits")
    if request_trees:
        # Gated up front, as the providers' own jobs are: a run that would refuse every request
        # halfway through is worse than a refusal now.
        for provider in dict.fromkeys(h["provider"] for h in hits):
            permission = await collection_request_permission(ctx, pool, user, provider)
            if not permission["allowed"]:
                raise ExportRefused(
                    403 if permission["available"] else 409, f"trees cannot be requested: {permission['reason']}"
                )
    for hit in hits:
        # The design request for a mesh file names the file it writes (the mesh's stem) and the mesh.
        # Never from the body: a key there could name anything in the target scope.
        if hit["node_id"] in (preset_files or {}):
            hit["file"], hit["mesh_file"] = preset_files[hit["node_id"]]
    batches = batch_hits(hits, folder, ext="glb" if mesh else "ifc", merge=merge)
    options = {
        "geometry": geometry,
        "merge": merge,
        "request_trees": request_trees,
        "include_source_attributes": include_attrs,
        "also": list(dict.fromkeys(also)),
        "request_missing_geometry": request_missing,
    }
    run = await db_module.insert_asset_export_run(
        pool,
        scope=scope_obj.prefix(),
        target_scope=str(body.get("target_scope")),
        target_folder=folder,
        query=query,
        options=options,
        batches=batches,
        requested_by=getattr(user, "sub", None),
    )
    job_opts = export_job_opts(run["id"], options, scope=scope, target=target, folder=folder, query=query, user=user)
    job_opts["write_index"] = body.get("write_index") is not False
    spawn(
        drive_export(ctx, pool, run, scope_obj=scope_obj, target=target, batches=batches, job_opts=job_opts, user=user),
        name=f"asset-export-{run['id']}",
    )
    logger.info("asset export %s: %d hit(s) in %d batch(es) -> %s", run["id"], len(hits), len(batches), folder)
    return run


# --- the preflight ----------------------------------------------------------------------------------


async def request_permission(pool, user, req: "greq.NodeRequest | None") -> tuple[bool, str | None]:
    """May ``user`` run the geometry provider's request job? The plugin route's own gate, asked
    up front so the dialog offers only what the start will accept."""
    if req is None:
        return False, f"no online worker of {GEOMETRY_PROVIDER!r} declares a node request"
    from .plugin_jobs import plugin_job_requires_admin

    if await plugin_job_requires_admin(req.plugin_id, pool, dict(req.spec)) and not getattr(user, "is_admin", False):
        return False, f"the {req.plugin_id!r} job is restricted to administrators"
    return True, None


#: Examples of unknown nodes listed per collection in a preflight.
PREFLIGHT_EXAMPLES = 5


async def export_preflight(ctx, pool, *, scope_obj: Scope, body: dict, user) -> dict:
    """What an export of ``body`` would find (see the module docstring). Writes nothing."""
    t0 = time.monotonic()
    query = body.get("query") if isinstance(body.get("query"), dict) else {}
    try:
        parsed = parse_search_query({k: v for k, v in query.items() if k not in ("cursor", "limit")})
    except SearchQueryError as exc:
        raise ExportRefused(400, f"the query: {exc}") from exc
    raw_hits = body.get("hits")
    if raw_hits is None:
        # Up to the LARGEST cap a run may take: the preflight writes nothing and does not know
        # whether the export will be merged, and refusing here at the per-file cap is what made a
        # merged export of a few thousand hits skip its preflight altogether. The start enforces the run's cap.
        raw_hits = await all_hits(pool, scope=scope_obj.prefix(), query_body=query, limit=MAX_MERGED_EXPORT_HITS)
    elif not isinstance(raw_hits, list):
        raise ExportRefused(400, "'hits' must be a list of search rows, or null for every match")
    where_keys = [w["key"] for w in parsed.where]
    if str(body.get("geometry") or GEOMETRY_DESIGN) == GEOMETRY_MESH:
        return await mesh_preflight(
            ctx, pool, scope_obj=scope_obj, hits=raw_hits, where_keys=where_keys, user=user, t0=t0
        )
    groups: dict[str, dict[str, dict]] = {}
    for hit in (clean_hit(r, where_keys) for r in raw_hits):
        if hit is not None:
            groups.setdefault(hit["collection"], {}).setdefault(hit["node_id"], hit)
    ordered = [(c, list(h.values())) for c, h in groups.items()]
    states = await greq.classify_in_scope(ctx.storage, scope_obj, ordered) if ordered else []

    req = await greq.geometry_node_request(ctx)
    allowed, why = await request_permission(pool, user, req)
    sweep = None
    try:
        sweep = await db_module.running_asset_sweep(pool, provider=GEOMETRY_PROVIDER)
    except Exception:  # noqa: BLE001 - a nicety for the prompt
        logger.debug("export preflight: could not read running sweeps", exc_info=True)

    collections = []
    for (collection, _), rows in zip(ordered, states):
        by: dict[str, list] = {}
        for s in rows:
            by.setdefault(s.state, []).append(s)
        missing = len(by.get("listed", ())) + len(by.get("unpublished", ()))
        unknown = by.get("unknown", [])
        collections.append(
            {
                "collection": collection,
                "hits": len(rows),
                "ready": len(by.get("ready", ())),
                "missing": missing,
                "listed": len(by.get("listed", ())),
                "unpublished": len(by.get("unpublished", ())),
                "unknown": len(unknown),
                "jobs": greq.request_estimate(missing),
                "missing_reason": next((s.message for s in rows if s.state in greq.MISSING_STATES), None),
                "unknown_reason": unknown[0].message if unknown else None,
                "unknown_examples": [s.hit.get("full_name") or s.hit["node_id"] for s in unknown[:PREFLIGHT_EXAMPLES]],
            }
        )
    return {
        "geometry_provider": GEOMETRY_PROVIDER,
        "hits": sum(c["hits"] for c in collections),
        "ready": sum(c["ready"] for c in collections),
        "missing": sum(c["missing"] for c in collections),
        "unknown": sum(c["unknown"] for c in collections),
        "jobs": sum(c["jobs"] for c in collections),
        "collections": collections,
        "request": {
            "available": req is not None,
            "allowed": allowed,
            "reason": why,
            "plugin_id": req.plugin_id if req else None,
            "label": req.label if req else None,
            "per_job": greq.NODES_PER_REQUEST,
        },
        "sweep_running": {"id": sweep["id"], "created_at": sweep.get("created_at")} if sweep else None,
        "took_ms": ms(time.monotonic() - t0),
    }


async def collection_request_permission(ctx, pool, user, provider: str) -> dict:
    """``{available, allowed, reason, label, plugin_id}`` of ``provider``'s collection request --
    what the mesh preflight offers for a collection it has published no tree of. The plugin route's
    own admin gate, asked up front."""
    from .asset_sweeps import sweep_entry
    from .plugin_jobs import plugin_job_requires_admin

    _, found = await sweep_entry(ctx, provider, "collection-request")
    if found is None:
        return {
            "available": False,
            "allowed": False,
            "reason": f"no online worker of {provider!r} declares a collection request",
            "label": None,
            "plugin_id": None,
        }
    spec, entry = found
    plugin_id = str(spec.get("slug") or spec.get("id") or "")
    allowed, reason = True, None
    if await plugin_job_requires_admin(plugin_id, pool, dict(spec)) and not getattr(user, "is_admin", False):
        allowed, reason = False, f"the {plugin_id!r} job is restricted to administrators"
    return {"available": True, "allowed": allowed, "reason": reason, "label": entry["label"], "plugin_id": plugin_id}


async def mesh_preflight(ctx, pool, *, scope_obj: Scope, hits: list, where_keys: list[str], user, t0: float) -> dict:
    """The preflight of a MESH export: each hit against ITS OWN provider's published tree -- the
    tree the mesh is cut from -- rather than the design system's geometry.

    The case it exists for: a search index is filled by one job (a provider's attribute sweep) and
    the tree a mesh export needs by another, so a fresh index answers thousands of hits a mesh export
    cannot draw. Per (provider, collection): ``ready`` hits, ``no_tree`` when the provider has
    published no project tree of the collection, and ``unknown`` for nodes its tree does not hold.
    Both are offered a COLLECTION request (``tree_requests``): a fresh tree is the fix for the first,
    and for the second when the tree is older than the node."""
    groups: dict[tuple[str, str], dict[str, dict]] = {}
    for hit in (clean_hit(r, where_keys) for r in hits):
        if hit is not None:
            groups.setdefault((hit["provider"], hit["collection"]), {}).setdefault(hit["node_id"], hit)
    by_provider: dict[str, list[tuple[str, list[dict]]]] = {}
    for (provider, collection), nodes in groups.items():
        by_provider.setdefault(provider, []).append((collection, list(nodes.values())))

    collections: list[dict] = []
    requests: dict[str, dict] = {}
    sweeps: dict[str, dict | None] = {}
    for provider, ordered in by_provider.items():
        states = await greq.classify_in_scope(ctx.storage, scope_obj, ordered, provider=provider)
        for (collection, _), rows in zip(ordered, states):
            by: dict[str, list] = {}
            for s in rows:
                by.setdefault(s.state, []).append(s)
            unknown = by.get("unknown", []) + by.get("listed", [])
            collections.append(
                {
                    "provider": provider,
                    "collection": collection,
                    "hits": len(rows),
                    "ready": len(by.get("ready", ())),
                    "no_tree": len(by.get("unpublished", ())),
                    "unknown": len(unknown),
                    "unknown_reason": unknown[0].message if unknown else None,
                    "unknown_examples": [
                        s.hit.get("full_name") or s.hit["node_id"] for s in unknown[:PREFLIGHT_EXAMPLES]
                    ],
                }
            )
        if any(c["no_tree"] or c["unknown"] for c in collections if c["provider"] == provider):
            requests[provider] = await collection_request_permission(ctx, pool, user, provider)
            try:
                sweeps[provider] = await db_module.running_asset_sweep(pool, provider=provider)
            except Exception:  # noqa: BLE001 - a nicety for the prompt
                sweeps[provider] = None
    return {
        "geometry": GEOMETRY_MESH,
        "hits": sum(c["hits"] for c in collections),
        "ready": sum(c["ready"] for c in collections),
        "no_tree": sum(c["no_tree"] for c in collections),
        "unknown": sum(c["unknown"] for c in collections),
        "collections": collections,
        "tree_requests": [
            {
                "provider": provider,
                "collections": [
                    c["collection"] for c in collections if c["provider"] == provider and (c["no_tree"] or c["unknown"])
                ],
                **request,
                "sweep_running": (
                    {"id": sweeps[provider]["id"], "created_at": sweeps[provider].get("created_at")}
                    if sweeps.get(provider)
                    else None
                ),
            }
            for provider, request in requests.items()
        ],
        "took_ms": ms(time.monotonic() - t0),
    }


@router.post("/scopes/{scope}/assets/search/export/request-trees")
async def api_search_export_request_trees(
    scope: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Body ``{provider, collections}``: request those collections' project trees from ``provider``
    -- its declared collection request, the Sources tab's "Request <collection>", once per
    collection -- as ONE run driven like a sweep (``asset_sweeps``): one row per collection, its
    jobs' timings, cancel and skip, and the sweep runs list to watch it in. Answers the run (202).

    Gated as the provider's job is: refused (403) when it is restricted to administrators and the
    user is not one, and (409) while a sweep of the same provider runs -- one run per provider."""
    from .asset_sweeps import COLLECTION_REQUEST, SweepRefused, start_sweep

    pool = require_pool(request)
    body = await request.json() if await request.body() else {}
    provider = str((body or {}).get("provider") or "").strip()
    wanted = (body or {}).get("collections")
    if not provider or not isinstance(wanted, list) or not wanted or not all(isinstance(c, str) for c in wanted):
        raise HTTPException(status_code=400, detail="'provider' and a non-empty list of 'collections' are required")
    permission = await collection_request_permission(ctx, pool, user, provider)
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
            job=COLLECTION_REQUEST,
            settings={},
            user=user,
            via="export",
            collections=wanted,
        )
    except SweepRefused as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return JSONResponse(run, status_code=202)


@router.get("/scopes/{scope}/assets/search/export/request-trees/{run_id}")
async def api_search_export_request_trees_run(
    scope: str,
    run_id: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """A tree request started from this scope, with its items' live progress -- what the toast
    polls. Only a collection request of THIS scope: the sweep reads behind it are an admin's."""
    from .asset_sweeps import COLLECTION_REQUEST, sweep_with_progress

    run = await sweep_with_progress(ctx, require_pool(request), run_id)
    if run is None or run.get("job") != COLLECTION_REQUEST or run.get("scope") != scope:
        raise HTTPException(status_code=404, detail="no such tree request in this scope")
    return JSONResponse(run)


@router.post("/scopes/{scope}/assets/search/export/preflight")
async def api_search_export_preflight(
    scope: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Body: ``{query, hits|null}`` as for the export. Answers per collection how many hits have
    geometry ready, how many lack it (and could be requested), and how many the geometry
    provider's tree does not know -- and whether this user may request them. Writes nothing."""
    pool = require_pool(request)
    body = await request.json() if await request.body() else {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="the body must be a JSON object")
    try:
        doc = await export_preflight(ctx, pool, scope_obj=scope_obj, body=body, user=user)
    except ExportRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return JSONResponse(doc)


# --- the driver -------------------------------------------------------------------------------------


def item_outcome(summary: dict) -> tuple[str, str | None]:
    """An item's status and message from its job's counts."""
    counts = summary.get("counts") or {}
    written, nogeo, failed = (int(counts.get(k) or 0) for k in ("written", "no_geometry", "failed"))
    total = written + nogeo + failed
    if written == total:
        return "done", None
    parts = [f"{written} of {total} written"]
    if nogeo:
        if summary.get("geometry") == GEOMETRY_MESH:
            parts.append(f"{nogeo} without a mesh from {summary.get('provider') or 'its provider'}")
        else:
            parts.append(f"{nogeo} without E3D geometry")
    if failed:
        parts.append(f"{failed} failed")
    if written == 0 and failed and not nogeo:
        return "error", "; ".join(parts)
    return "partial", "; ".join(parts)


def export_index_document(
    run: dict, files_by_position: dict[int, list[dict]], folder: str, seed: list[dict] | None = None
) -> dict:
    """``seed``: the folder's entries before a re-run. A re-written file replaces its entry; a new
    one goes after the last entry of its (provider, collection), else at the end."""
    files = list(seed or ())
    for position in sorted(files_by_position):
        for f in files_by_position[position]:
            same = next((i for i, e in enumerate(files) if e.get("file") == f.get("file")), None)
            if same is not None:
                files[same] = f
                continue
            batch = (f.get("provider"), f.get("collection"))
            last = max(
                (i for i, e in enumerate(files) if (e.get("provider"), e.get("collection")) == batch), default=None
            )
            files.insert(len(files) if last is None else last + 1, f)
    return {
        "schema": EXPORT_INDEX_SCHEMA,
        "run_id": run["id"],
        "query": run.get("query"),
        "created_at": run.get("created_at"),
        "folder": folder,
        "files": files,
    }


async def drive_export(
    ctx,
    pool,
    run: dict,
    *,
    scope_obj: Scope,
    target: Scope,
    batches,
    job_opts: dict,
    user,
    positions: list[int] | None = None,
    carried: dict[int, int] | None = None,
    index_seed: list[dict] | None = None,
) -> None:
    """``positions``: each batch's item (a re-run drives some items of a run); default 0..n-1.
    ``carried``: per position, files a previous pass of the item already wrote. ``index_seed``: the
    folder index the re-written files are merged into."""
    run_id = run["id"]
    positions = positions if positions is not None else list(range(len(batches)))
    gate = asyncio.Semaphore(EXPORT_CONCURRENCY)
    index_lock = asyncio.Lock()
    # One item's geometry request at a time: each is one collection (one design project), and two
    # in flight would make a shared seat switch projects between their jobs.
    request_gate = asyncio.Lock()
    files_by_position: dict[int, list[dict]] = {}
    folder = job_opts["target_folder"]

    async def write_index() -> None:
        doc = export_index_document(run, files_by_position, folder, index_seed)
        try:
            await ctx.storage.put_bytes(
                target, f"{folder}/{EXPORT_INDEX_FILENAME}", json.dumps(doc, indent=1).encode("utf-8")
            )
        except Exception:  # noqa: BLE001 - the index is a convenience; the files are written
            logger.warning("asset export %s: could not write %s", run_id, EXPORT_INDEX_FILENAME, exc_info=True)

    async def one(position: int, provider: str, collection: str, hits: list[dict]) -> bool:
        async with gate:
            status = await db_module.heartbeat_asset_export_run(pool, run_id)
            if status != "running":
                raise RunCancelled
            ok, files = await export_one(
                ctx,
                pool,
                run_id,
                position,
                provider,
                collection,
                hits,
                scope_obj=scope_obj,
                job_opts=job_opts,
                user=user,
                request_gate=request_gate,
                carried_written=(carried or {}).get(position, 0),
            )
        if files and job_opts.get("write_index", True):
            async with index_lock:
                files_by_position[position] = files
                await write_index()
        return ok

    tasks = [asyncio.create_task(one(i, p, c, h)) for i, (p, c, h) in zip(positions, batches)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    if any(isinstance(r, RunCancelled) for r in results):
        await db_module.skip_pending_asset_export_items(pool, run_id, "the export was cancelled")
        logger.info("asset export %s: cancelled", run_id)
        return
    crashed = [r for r in results if isinstance(r, BaseException)]
    if crashed:
        logger.error("asset export %s: driver crashed: %r", run_id, crashed[0])
        await db_module.skip_pending_asset_export_items(pool, run_id, "the export stopped early")
        await db_module.finish_asset_export_run(pool, run_id, status="error", error=f"the export crashed: {crashed[0]}")
        return
    failed = sum(1 for r in results if r is False)
    error = f"{failed} of {len(batches)} batch(es) failed" if failed else None
    await db_module.finish_asset_export_run(pool, run_id, status="error" if failed else "done", error=error)
    logger.info("asset export %s: finished (%s)", run_id, error or "all ok")


async def export_one(
    ctx,
    pool,
    run_id: str,
    position: int,
    provider: str,
    collection: str,
    hits: list[dict],
    *,
    scope_obj: Scope,
    job_opts: dict,
    user,
    request_gate: asyncio.Lock | None = None,
    carried_written: int = 0,
) -> tuple[bool, list[dict]]:
    """One batch: (when asked) request its missing geometry, submit its job, wait, copy the
    summary into the item. Returns ``(ok, files)`` where ``files`` are the ``_export.json`` entries
    of what it wrote. ``carried_written``: files of this item a previous pass wrote, counted in.
    Raises only RunCancelled."""
    t_item = time.monotonic()
    timings: dict = {}
    requested: greq.RequestOutcome | None = None

    async def item(**fields) -> None:
        await db_module.update_asset_export_item(pool, run_id, position, **fields)

    async def finish(status: str, **fields) -> None:
        timings["total_ms"] = ms(time.monotonic() - t_item)
        await item(status=status, timings=timings, finished_at=datetime.now(timezone.utc), **fields)

    def wait(job_id: str, timeout_s: float):
        return wait_for_job(
            ctx,
            job_id,
            timeout_s=timeout_s,
            beat=lambda: db_module.heartbeat_asset_export_run(pool, run_id),
            position=position,
            poll_s=POLL_INTERVAL_S,
            label=f"asset export {run_id}",
        )

    await item(status="running", started_at=datetime.now(timezone.utc))
    derived_key = f"_derived/search-export/{run_id}/{position}.json"
    try:
        if job_opts.get("request_missing_geometry"):
            async with request_gate or asyncio.Lock():
                requested = await request_geometry(
                    ctx,
                    pool,
                    run_id,
                    position,
                    collection,
                    hits,
                    scope_obj=scope_obj,
                    user=user,
                    wait=wait,
                    timings=timings,
                )
            await item(status="running", message=requested.sentence() if requested else None, timings=timings)
        if job_opts.get("geometry") == GEOMETRY_MESH:
            hits = await build_meshes(
                ctx,
                pool,
                run_id,
                position,
                provider,
                collection,
                hits,
                scope_obj=scope_obj,
                user=user,
                timings=timings,
                request_tree=bool(job_opts.get("request_trees")),
            )
            await item(status="running", timings=timings)
        submitted = await ctx.jobs.submit(
            JobRequest(
                source_key=f"search-export/{run_id}/{position}",
                target_format=EXPORT_SEARCH_KIND,
                scope=scope_obj,
                feature="export_search",
                derived_prefix=derived_key.rsplit("/", 1)[0],
                derived_key=derived_key,
                conversion_options={
                    **job_opts,
                    "position": position,
                    "provider": provider,
                    "collection": collection,
                    "hits": hits,
                },
                force_rebuild=True,
            ),
            before_dispatch=lambda s: ctx.audit(
                None,
                user,
                scope_obj,
                "export_search",
                key=derived_key,
                target_format=EXPORT_SEARCH_KIND,
                status="queued",
                job_id=s.job_id,
            ),
        )
        await item(job_id=submitted.job_id)
        timings["queued_ms"], timings["run_ms"] = await wait(submitted.job_id, ITEM_TIMEOUT_S)
        summary = json_of(await ctx.storage.get_bytes(scope_obj, derived_key))
        if not isinstance(summary, dict):
            raise RuntimeError("the export job answered without a summary")
        timings["job"] = summary.get("timings") or {}
        if carried_written:
            counts = dict(summary.get("counts") or {})
            counts["written"] = int(counts.get("written") or 0) + carried_written
            summary["counts"] = counts
        status, message = item_outcome(summary)
        file_rows = summary.get("files") or []
        if requested is not None:
            # A hit the provider was asked for and answered "nothing" for: its words, not the
            # resolver's "publish its geometry first" -- that was just tried.
            for f in file_rows:
                said = requested.nothing.get(str(f.get("node_id")))
                if f.get("status") == "no_geometry" and said:
                    f["error"] = f"the geometry provider has nothing for it: {said}"
            message = "; ".join(filter(None, [requested.sentence(), message]))
        errors = [
            {k: f.get(k) for k in ("node_id", "full_name", "status", "error")}
            for f in file_rows
            if f.get("status") != "written"
        ]
        warnings = summary.get("warnings") or []
        if warnings:
            message = "; ".join(filter(None, [message, *warnings]))
        await finish(status, files=summary.get("counts") or {}, file_errors=errors, message=message)
        merged = summary.get("merged")
        if isinstance(merged, dict) and merged.get("file"):
            # One file for the batch: one entry in the folder's index, saying how many hits it holds.
            written = [
                {
                    "file": merged["file"],
                    "provider": provider,
                    "collection": collection,
                    "node_id": None,
                    "full_name": collection,
                    "hits": merged.get("hits"),
                    "meta": merged.get("meta") or meta_sidecar_key(merged["file"]),
                    "thumb": merged.get("thumb"),
                    # The hits inside it, in file order, each with its own thumbnail: what the
                    # gallery steps through within the file.
                    "joints": merged.get("joints") or [],
                }
            ]
            return status != "error", written
        written = [
            {
                "file": f["file"],
                "provider": provider,
                "collection": collection,
                "node_id": f.get("node_id"),
                "full_name": f.get("full_name"),
                "meta": f.get("meta") or meta_sidecar_key(f["file"]),
                "thumb": f.get("thumb"),
            }
            for f in file_rows
            if f.get("status") == "written" and not f.get("merged")
        ]
        return status != "error", written
    except RunCancelled:
        await finish("skipped", message="the export was cancelled")
        raise
    except ItemSkipped as exc:
        await db_module.clear_asset_export_skip(pool, run_id, position)
        await finish("skipped", message=str(exc))
        return True, []
    except HTTPException as exc:
        await finish("error", error=f"{exc.status_code}: {exc.detail}")
    except Exception as exc:  # noqa: BLE001 - one batch's failure is its row's
        logger.warning("asset export %s: batch %s/%s failed: %s", run_id, provider, collection, exc)
        await finish("error", error=str(exc))
    return False, []


#: Hit states a fresh collection tree can resolve: no tree, a tree without the node, a node
#: listed without geometry.
TREE_REQUEST_STATES = ("unpublished", "unknown", "listed")
#: Mesh builds of one item in flight at once. Each reads its site GLB (cached after the first) and
#: cuts one node out of it; no seat stands behind them.
MESH_BUILDS_IN_FLIGHT = 8
#: One node's mesh build: minutes for the first of a site (the site GLB download), seconds after.
MESH_BUILD_TIMEOUT_S = 30 * 60


async def request_collection_tree(
    ctx,
    pool,
    run_id: str,
    position: int,
    provider: str,
    collection: str,
    *,
    scope_obj: Scope,
    user,
    timings: dict,
) -> str | None:
    """Ask ``provider`` for ``collection``'s project tree -- its declared collection request, the
    Sources tab's "Request <collection>" -- and publish the answer, inside this export item. Returns
    None when a tree was published, else what the provider (or the lack of one) said instead, which
    becomes every hit's reason. Timed as ``tree_request_ms``.

    The same two jobs a sweep runs for one collection (``asset_sweeps._sweep_one``), driven here so
    the export that needs the tree is the run the user watches: "request trees, then export" is one
    run in the toast, not a request to wait for and an export to remember to start."""
    from .asset_schedules import build_options, job_keys
    from .asset_sweeps import (
        COLLECTION_REQUEST,
        FETCH_TIMEOUT_S,
        PUBLISH_TIMEOUT_S,
        _all_collections,
        _provider_options,
        offered_names,
        sweep_entry,
    )
    from .assets import staged_by_role, submit_asset_publish
    from .plugin_jobs import enqueue_plugin_job

    specs, found = await sweep_entry(ctx, provider, COLLECTION_REQUEST)
    if found is None:
        return f"no online worker of {provider!r} declares a collection request"
    spec, entry = found
    plugin_id = str(spec.get("slug") or spec.get("id") or "")
    names, _ = offered_names(_all_collections(specs), [collection])
    name = names[0] if names else collection
    t0 = time.monotonic()
    await db_module.update_asset_export_item(
        pool, run_id, position, status="requesting", message=f"requesting the {name} project tree from {provider}"
    )

    def wait(job_id: str, timeout_s: float):
        return wait_for_job(
            ctx,
            job_id,
            timeout_s=timeout_s,
            beat=lambda: db_module.heartbeat_asset_export_run(pool, run_id),
            position=position,
            poll_s=POLL_INTERVAL_S,
            label=f"asset export {run_id} tree",
        )

    try:
        options = await build_options(
            pool, spec, entry, scope=scope_obj.prefix(), provider=provider, collection=name, settings={}, validate=False
        )
        options = {
            **await _provider_options(ctx, scope_obj, provider, name),
            **options,
            "requested_at": datetime.now(timezone.utc).isoformat(),
        }
        derived_key, _ = job_keys(plugin_id, options)
        job_id = await enqueue_plugin_job(
            ctx,
            plugin_id=plugin_id,
            options=options,
            scope_obj=scope_obj,
            user=user,
            pool=pool,
            derived_key=derived_key,
            plugin_spec=spec,
        )
        await wait(job_id, FETCH_TIMEOUT_S)
        summary = json_of(await ctx.storage.get_bytes(scope_obj, derived_key))
        if not isinstance(summary, dict):
            return f"{provider}'s tree request answered without a summary"
        if summary.get("asset_nothing") is True or summary.get("asset_unchanged") is True:
            return f"{provider} had no tree to give for {name}: {summary.get('message') or 'nothing staged'}"
        staging_id = summary.get("asset_staging_id")
        if not isinstance(staging_id, str) or not staging_id.strip():
            return f"{provider}'s tree request finished without anything staged to publish"
        await db_module.update_asset_export_item(
            pool, run_id, position, status="requesting", message=f"publishing the {name} project tree"
        )
        staged = await staged_by_role(ctx, scope_obj, staging_id.strip())
        publish_job, _ = await submit_asset_publish(ctx, scope_obj, provider=provider, staged=staged, user=user)
        await wait(publish_job, PUBLISH_TIMEOUT_S)
        return None
    except (RunCancelled, ItemSkipped):
        raise
    except Exception as exc:  # noqa: BLE001 - the request failing is the hits' reason, not the run's crash
        logger.warning("asset export %s: tree request of %s/%s failed: %s", run_id, provider, name, exc)
        return f"requesting the {name} project tree from {provider} failed: {exc}"
    finally:
        timings["tree_request_ms"] = ms(time.monotonic() - t0)


async def build_meshes(
    ctx,
    pool,
    run_id: str,
    position: int,
    provider: str,
    collection: str,
    hits: list[dict],
    *,
    scope_obj: Scope,
    user,
    timings: dict,
    request_tree: bool = False,
) -> list[dict]:
    """The mesh item's build phase: each hit resolved against what ``provider`` -- the hits' own,
    whose attribute index found them -- published in the collection, and built through the ordinary
    asset build (``assets.submit_node_build`` -- the same job and the same derived key "Load into
    scene" uses, so a node already looked at is not built again).

    Returns the hits, each with ``mesh`` (``{glb_key, provider, subject, revision, node}``) or
    ``mesh_error`` -- the export job writes the first and reports the second per file. Timed as
    ``build_resolve_ms`` and ``build_ms`` (wall-clock over the item's builds), with how many were
    already built (``builds_cached``)."""
    from .assets import submit_node_build

    async def progress(message: str) -> None:
        await db_module.update_asset_export_item(
            pool, run_id, position, status="running", message=message, timings=timings
        )

    t0 = time.monotonic()
    await progress(f"finding the {provider} meshes")
    [states] = await greq.classify_in_scope(ctx.storage, scope_obj, [(collection, hits)], provider=provider)
    timings["build_resolve_ms"] = ms(time.monotonic() - t0)
    if request_tree and any(s.state in TREE_REQUEST_STATES for s in states):
        # ``request_trees``: no tree to cut from, or one without these hits -- ask for a fresh one,
        # then look again.
        why_not = await request_collection_tree(
            ctx, pool, run_id, position, provider, collection, scope_obj=scope_obj, user=user, timings=timings
        )
        await progress(f"finding the {provider} meshes")
        [states] = await greq.classify_in_scope(ctx.storage, scope_obj, [(collection, hits)], provider=provider)
        if why_not:
            for s in states:
                if s.state == "unpublished":
                    s.message = why_not

    gate = asyncio.Semaphore(MESH_BUILDS_IN_FLIGHT)
    done = 0
    cached = 0
    ready = [s for s in states if s.state == "ready"]

    async def one(state) -> dict:
        nonlocal done, cached
        hit = dict(state.hit)
        if state.state != "ready":
            hit["mesh_error"] = state.message or f"{provider} published no mesh covering it"
            return hit
        async with gate:
            try:
                answer = await submit_node_build(
                    ctx,
                    scope_obj,
                    asked_provider=provider,
                    collection=collection,
                    node=str(state.node_id),
                    subject=str(state.subject or state.node_id),
                    user=user,
                )
                if answer.get("job_id"):
                    await wait_for_job(
                        ctx,
                        answer["job_id"],
                        timeout_s=MESH_BUILD_TIMEOUT_S,
                        beat=lambda: db_module.heartbeat_asset_export_run(pool, run_id),
                        position=position,
                        poll_s=POLL_INTERVAL_S,
                        label=f"asset export {run_id} mesh",
                    )
                else:
                    cached += 1
                summary = json_of(await ctx.storage.get_bytes(scope_obj, answer["derived_key"]))
            except (RunCancelled, ItemSkipped):
                raise
            except HTTPException as exc:
                hit["mesh_error"] = f"the mesh build was refused: {exc.detail}"
                return hit
            except Exception as exc:  # noqa: BLE001 - one node's build is its file's outcome
                hit["mesh_error"] = f"the mesh build failed: {exc}"
                return hit
            finally:
                done += 1
                if done % 5 == 0 or done == len(ready):
                    await progress(f"built {done} of {len(ready)} {provider} mesh(es)")
        if not isinstance(summary, dict) or not summary.get("ok") or not summary.get("glb_key"):
            hit["mesh_error"] = (summary or {}).get("error") or "the mesh build drew nothing"
            return hit
        hit["mesh"] = {
            "glb_key": summary["glb_key"],
            "provider": answer.get("provider") or provider,
            "subject": answer.get("subject"),
            "revision": answer.get("revision"),
            "node": answer.get("node"),
        }
        return hit

    t1 = time.monotonic()
    out = list(await asyncio.gather(*(one(s) for s in states)))
    timings["build_ms"] = ms(time.monotonic() - t1)
    timings["builds"] = len(ready)
    timings["builds_cached"] = cached
    return out


async def request_geometry(
    ctx,
    pool,
    run_id: str,
    position: int,
    collection: str,
    hits: list[dict],
    *,
    scope_obj: Scope,
    user,
    wait,
    timings: dict,
) -> greq.RequestOutcome:
    """The item's request phase: which hits lack geometry, then ``geometry_requests.request_missing``
    for them. Timed as ``request_ms`` (the request jobs, queued + run) and ``request_publish_ms``
    (publishing their answers), with one ``request_batches`` row per job -- its node count, the
    provider's login mode and phases -- so "one project session" is something the run shows."""

    async def progress(message: str, job_id: str | None) -> None:
        fields: dict = {"status": "requesting", "message": message, "timings": timings}
        if job_id:
            fields["job_id"] = job_id  # so the run's read shows the request job's live stage
        await db_module.update_asset_export_item(pool, run_id, position, **fields)

    t0 = time.monotonic()
    await progress("checking which hits lack geometry", None)
    [states] = await greq.classify_in_scope(ctx.storage, scope_obj, [(collection, hits)])
    timings["request_resolve_ms"] = ms(time.monotonic() - t0)
    req = await greq.geometry_node_request(ctx)
    if req is None:
        outcome = greq.RequestOutcome(
            missing=sum(1 for s in states if s.state in greq.MISSING_STATES),
            unknown=sum(1 for s in states if s.state == "unknown"),
        )
        if outcome.missing:
            outcome.batches.append(
                {"nodes": outcome.missing, "status": "error", "message": "no online worker declares a node request"}
            )
    else:
        outcome = await greq.request_missing(
            ctx,
            pool,
            scope_obj=scope_obj,
            user=user,
            req=req,
            collection=collection,
            states=states,
            wait=wait,
            progress=progress,
        )
    timings["request_ms"] = outcome.request_ms
    timings["request_publish_ms"] = outcome.publish_ms
    timings["request_batches"] = outcome.batches
    timings["request"] = outcome.summary()
    return outcome


# --- runs -------------------------------------------------------------------------------------------


#: Item states with a job in flight: ``requesting`` (asking the geometry provider) and ``running``.
_IN_FLIGHT = ("requesting", "running")


def _as_wire(run: dict, scope: str) -> dict:
    """A run as the caller sees it: ``scope`` in the WIRE form it asked with (``user:me``), so a
    client can call this API with it again. The row keeps the storage prefix, which is what
    separates one user's personal runs from another's."""
    return {**run, "scope": scope}


async def _run_in_scope(pool, run_id: str, scope: str) -> dict:
    run = await db_module.get_asset_export_run(pool, run_id)
    if run is None or run["scope"] != scope:  # the scope's storage prefix, never its wire form
        raise HTTPException(status_code=404, detail="export run not found")
    return run


@router.get("/scopes/{scope}/assets/search/export/runs")
async def api_search_export_runs(
    scope: str, request: Request, limit: int = 20, scope_obj: Scope = Depends(scope_from_path)
) -> JSONResponse:
    pool = require_pool(request)
    runs = await db_module.list_asset_export_runs(pool, scope=scope_obj.prefix(), limit=max(1, min(int(limit), 100)))
    return JSONResponse({"runs": [_as_wire(r, scope) for r in runs]})


@router.get("/scopes/{scope}/assets/search/export/runs/{run_id}")
async def api_search_export_run(
    scope: str,
    run_id: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """One run with its items; an item in flight carries its job's live ``stage``/``progress``."""
    pool = require_pool(request)
    run = await _run_in_scope(pool, run_id, scope_obj.prefix())
    for item in run["items"]:
        if item["status"] not in _IN_FLIGHT or not item["job_id"]:
            continue
        try:
            snap = await ctx.jobs.status(item["job_id"])
        except Exception:  # noqa: BLE001 - live progress is a nicety
            continue
        if snap is not None:
            item["job_status"] = snap.status
            item["stage"] = (snap.payload or {}).get("stage")
            item["progress"] = (snap.payload or {}).get("progress")
    return JSONResponse(_as_wire(run, scope))


@router.post("/scopes/{scope}/assets/search/export/runs/{run_id}/cancel")
async def api_search_export_cancel(
    scope: str,
    run_id: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    pool = require_pool(request)
    run = await _run_in_scope(pool, run_id, scope_obj.prefix())
    if not await db_module.cancel_asset_export_run(pool, run_id):
        raise HTTPException(status_code=409, detail="that export is not running")
    await db_module.skip_pending_asset_export_items(pool, run_id, "the export was cancelled")
    # The jobs in flight are cancelled HERE as well as by the driver: a driver that died with its
    # API process never sees the cancel, and its queued jobs -- geometry requests on a single-seat
    # provider in particular -- would otherwise run for nobody. Same reasoning as a sweep's cancel.
    cancelled_jobs = []
    for item in run["items"]:
        if item["status"] not in _IN_FLIGHT:
            continue
        batches = (item.get("timings") or {}).get("request_batches") or ()
        job_ids = {item.get("job_id"), *(b.get("job_id") for b in batches if isinstance(b, dict))}
        for job_id in sorted(j for j in job_ids if j):
            try:
                if await ctx.jobs.cancel(job_id):
                    cancelled_jobs.append(job_id)
            except Exception:  # noqa: BLE001 - the run is cancelled either way
                logger.warning("export %s: could not cancel job %s", run_id, job_id, exc_info=True)
    return JSONResponse({"cancelled": run_id, "jobs": cancelled_jobs})


@router.post("/scopes/{scope}/assets/search/export/runs/{run_id}/skip")
async def api_search_export_skip(
    scope: str,
    run_id: str,
    request: Request,
    position: int,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Skip ONE batch in flight (items run concurrently, so ``position`` is required); the rest of
    the export goes on."""
    pool = require_pool(request)
    run = await _run_in_scope(pool, run_id, scope_obj.prefix())
    if run["status"] != "running":
        raise HTTPException(status_code=409, detail=f"that export is {run['status']}, not running")
    item = next((i for i in run["items"] if i["position"] == position), None)
    if item is None or item["status"] not in _IN_FLIGHT:
        raise HTTPException(status_code=409, detail=f"batch {position} is not running")
    if not await db_module.request_asset_export_skip(pool, run_id, position, by=user_label(user)):
        raise HTTPException(status_code=409, detail="that export is not running")
    return JSONResponse({"skip_requested": position})


def rerun_plan(run: dict) -> list[tuple[int, str, str, list[dict], int]]:
    """``[(position, provider, collection, hits, carried_written)]``: what a re-run drives -- every
    item not ``done``. A merged item is driven whole (its one file holds every hit); otherwise only
    the hits without a written file, counting in the ones the item wrote."""
    merged = bool((run.get("options") or {}).get("merge"))
    plan = []
    for item in run.get("items") or ():
        if item.get("status") == "done":
            continue
        hits = list(item.get("hit_rows") or ())
        carried = 0
        failed = {str(e.get("node_id")) for e in item.get("file_errors") or () if e.get("node_id") is not None}
        if not merged and failed and not item.get("error"):
            hits = [h for h in hits if str(h.get("node_id")) in failed]
            carried = int((item.get("files") or {}).get("written") or 0)
        if hits:
            plan.append((int(item["position"]), item["provider"], item["collection"], hits, carried))
    return plan


@router.post("/scopes/{scope}/assets/search/export/runs/{run_id}/rerun")
async def api_search_export_rerun(
    scope: str,
    run_id: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Re-export what a finished run did not write, into the same run, folder and files
    (:func:`rerun_plan`). Idempotent: a running run, or one with nothing left to write, is answered
    as it is, with ``rerun: false`` and why. A mesh run requests fresh trees when the user may."""
    pool = require_pool(request)
    ctx.jobs.require("export_search")
    run = await db_module.get_asset_export_run(pool, run_id, with_hits=True)
    if run is None or run["scope"] != scope_obj.prefix():
        raise HTTPException(status_code=404, detail="export run not found")

    def answer(rerun: bool, reason: str | None = None) -> JSONResponse:
        shown = {**run, "items": [{k: v for k, v in i.items() if k != "hit_rows"} for i in run["items"]]}
        return JSONResponse({**_as_wire(shown, scope), "rerun": rerun, "reason": reason})

    if run["status"] == "running":
        return answer(False, "the export is running")
    plan = rerun_plan(run)
    if not plan:
        return answer(False, "nothing to re-export: every file was written")
    try:
        target = await _writable_scope(pool, run["target_scope"], user)
    except ExportRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    options = dict(run.get("options") or {})
    if options.get("geometry") == GEOMETRY_MESH:
        ctx.jobs.require("asset_build")
        providers = dict.fromkeys(provider for _, provider, _, _, _ in plan)
        allowed = [await collection_request_permission(ctx, pool, user, p) for p in providers]
        options["request_trees"] = options.get("request_trees") or all(a["allowed"] for a in allowed)
    if not await db_module.restart_asset_export_run(pool, run_id, [p for p, *_ in plan]):
        return answer(False, "the export is running")
    folder = run["target_folder"]
    seed = None
    try:
        doc = json_of(await ctx.storage.get_bytes(target, f"{folder}/{EXPORT_INDEX_FILENAME}"))
        seed = doc.get("files") if isinstance(doc, dict) else None
    except Exception:  # noqa: BLE001 - no index yet: the re-run writes one
        seed = None
    job_opts = export_job_opts(
        run_id, options, scope=scope, target=target, folder=folder, query=run.get("query") or {}, user=user
    )
    spawn(
        drive_export(
            ctx,
            pool,
            run,
            scope_obj=scope_obj,
            target=target,
            batches=[(p, c, h) for _, p, c, h, _ in plan],
            job_opts=job_opts,
            user=user,
            positions=[pos for pos, *_ in plan],
            carried={pos: n for pos, _, _, _, n in plan if n},
            index_seed=seed or [],
        ),
        name=f"asset-export-{run_id}-rerun",
    )
    logger.info("asset export %s: re-run of %d item(s)", run_id, len(plan))
    run = await db_module.get_asset_export_run(pool, run_id, with_hits=True) or run
    return answer(True)


# --- per-file metadata ------------------------------------------------------------------------------


def _meta_from_ifc_psets(key: str, found: dict) -> dict:
    prov = found.get("provenance") or {}
    src = found.get("source") or {}
    query: Any = prov.get("query")
    if isinstance(query, str):
        try:
            query = json.loads(query)
        except ValueError:
            pass
    return meta_document(
        file=key,
        fmt="ifc",
        created_by=None,
        created_at=prov.get("exported_at"),
        source={
            "provider": src.get("provider") or prov.get("provider"),
            "scope": src.get("scope") or prov.get("source_scope"),
            "collection": src.get("collection") or prov.get("collection"),
            "node_id": src.get("node_id"),
            "full_name": src.get("full_name"),
            "source_ref": src.get("source_ref"),
            "revision": prov.get("revision"),
        },
        query=query,
        matched=found.get("matched") or {},
        run_id=prov.get("run_id"),
        attributes={},
        derived={},
        elements=found.get("elements") or [],
    )


def _meta_from_glb(key: str, prov: dict) -> dict:
    query: Any = prov.get("query")
    if isinstance(query, str):
        try:
            query = json.loads(query)
        except ValueError:
            pass
    return meta_document(
        file=key,
        fmt="glb",
        created_by=None,
        created_at=prov.get("exported_at") or prov.get("built_at"),
        source={
            "provider": prov.get("provider"),
            "scope": prov.get("source_scope"),
            "collection": prov.get("collection"),
            "node_id": prov.get("node_id") or prov.get("node"),
            "full_name": prov.get("full_name"),
            "revision": prov.get("revision"),
        },
        query=query,
        matched={},
        run_id=prov.get("run_id"),
        attributes={},
        derived={},
    )


async def file_meta(storage, scope_obj: Scope, key: str) -> dict | None:
    """The sidecar, else in-file provenance, else None."""
    clean = key.strip().lstrip("/")
    candidates = [clean] if clean.endswith(".meta.json") else [meta_sidecar_key(clean)]
    for cand in candidates:
        try:
            doc = json_of(await storage.get_bytes(scope_obj, cand))
        except Exception:  # noqa: BLE001 - absent or unreadable: try the file itself
            continue
        if isinstance(doc, dict) and doc.get("schema") == META_SCHEMA:
            if doc.get("derived") is not None and not doc["derived"].get("thumb"):
                thumb = thumb_sidecar_key(clean)
                if await storage.exists(scope_obj, thumb):
                    doc["derived"]["thumb"] = thumb
            return {**doc, "key": clean, "origin": "sidecar"}

    ext = pathlib.PurePosixPath(clean).suffix.lower()
    if ext not in (".ifc", ".glb"):
        return None
    head = await storage.head(scope_obj, clean)
    if head is None or int(head.get("size") or 0) > META_FALLBACK_MAX_BYTES:
        return None
    if ext == ".glb":
        from ada.assets.build import read_glb_provenance

        prov = read_glb_provenance(await storage.get_bytes(scope_obj, clean))
        return {**_meta_from_glb(clean, prov), "key": clean, "origin": "glb"} if prov else None

    import tempfile

    from ..search_export import parse_ifc_provenance

    tmp = pathlib.Path(tempfile.mkdtemp(prefix="ada-meta-")) / "file.ifc"
    try:
        await storage.stream_to_path(scope_obj, clean, tmp)
        found = await asyncio.get_running_loop().run_in_executor(None, parse_ifc_provenance, tmp)
    except Exception:  # noqa: BLE001 - an unreadable IFC has no provenance to report
        logger.debug("files/meta: could not read %s", clean, exc_info=True)
        found = None
    finally:
        tmp.unlink(missing_ok=True)
        tmp.parent.rmdir()
    return {**_meta_from_ifc_psets(clean, found), "key": clean, "origin": "ifc"} if found else None


@router.get("/scopes/{scope}/files/meta")
async def api_file_meta(
    key: str,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """``ada.files/meta@1`` for a file of this scope (see the module docstring)."""
    if not key or not key.strip("/"):
        raise HTTPException(status_code=400, detail="'key' is required")
    doc = await file_meta(ctx.storage, scope_obj, key)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"no provenance for {key!r}")
    return JSONResponse(doc)


@router.post("/scopes/{scope}/files/request-geometry")
async def api_file_request_geometry(
    scope: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Body: ``{key}`` -- a mesh file a search export wrote (``meta.geometry.kind == "mesh"``).

    Starts a ONE-HIT design export of the node the file came from: the design system's geometry is
    requested first when it is missing (and the user may run the request), then written as
    ``<stem>.ifc`` beside the mesh GLB, in the same folder. The two share a stem and therefore a
    sidecar, which from then on is the IFC's and names the mesh as ``derived.mesh``. Answers the
    export run (202), which shows in the scope's export runs like any other."""
    pool = require_pool(request)
    ctx.jobs.require("export_search")
    body = await request.json() if await request.body() else {}
    key = str((body or {}).get("key") or "").strip().lstrip("/")
    if not key:
        raise HTTPException(status_code=400, detail="'key' is required")
    meta = await file_meta(ctx.storage, scope_obj, key)
    if meta is None:
        raise HTTPException(status_code=404, detail=f"no provenance for {key!r}")
    geometry = meta.get("geometry") or {}
    if geometry.get("kind") != GEOMETRY_MESH:
        raise HTTPException(status_code=409, detail=f"{key!r} is not a mesh export: it has its design geometry already")
    if geometry.get("merged"):
        raise HTTPException(
            status_code=409,
            detail=f"{key!r} merges {len(geometry.get('nodes') or [])} nodes into one file; design geometry is "
            "requested one node at a time -- export the ones you need without merging",
        )
    src = meta.get("source") or {}
    if not (src.get("provider") and src.get("collection") and src.get("node_id") and src.get("scope")):
        raise HTTPException(status_code=409, detail=f"{key!r} does not say which node it came from")

    from ..scope import can_access

    source_obj = await resolve_project_scope(pool, parse_scope(str(src["scope"]), user))
    if not await can_access(user, source_obj, pool):
        raise HTTPException(status_code=403, detail=f"you cannot read the source scope {src['scope']!r}")
    req = await greq.geometry_node_request(ctx)
    may_request, _why = await request_permission(pool, user, req)
    folder = key.rsplit("/", 1)[0] if "/" in key else ""
    hit = {
        "provider": src["provider"],
        "collection": src["collection"],
        "node_id": src["node_id"],
        "full_name": src.get("full_name"),
        "source_ref": src.get("source_ref"),
        "path": src.get("path") or [],
        "attrs": {},
    }
    try:
        run = await start_export(
            ctx,
            pool,
            scope=str(src["scope"]),
            scope_obj=source_obj,
            body={
                "query": meta.get("query") if isinstance(meta.get("query"), dict) else {},
                "hits": [hit],
                "target_scope": scope,
                "target_folder": folder,
                "geometry": GEOMETRY_DESIGN,
                "include_source_attributes": True,
                "request_missing_geometry": may_request,
                # One file into a folder another run indexed: that run's _export.json stays its own.
                "write_index": False,
            },
            user=user,
            preset_files={str(src["node_id"]): (sidecar_key(key, ".ifc"), key)},
        )
    except ExportRefused as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return JSONResponse({**_as_wire(run, str(src["scope"])), "requesting": may_request}, status_code=202)
