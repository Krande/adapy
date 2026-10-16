"""Admin -> Database: what the database holds per feature area, pruning old history, retention.

``GET  /api/admin/db/storage``    total size; every table by area (partitions under their parent)
                                  with rows, table/index/TOAST bytes and age range; the attribute
                                  index's partitions per collection; the retention settings.
``GET  /api/admin/db/tables/{table}/rows?offset=0&limit=50``
                                  one page (limit <= 200) of a table the storage view reports,
                                  read-only, bounded and with secrets redacted
                                  (``db/table_rows.py``); 404 for any other name.
``POST /api/admin/db/prune``      ``{"areas": {area: {"older_than_days": n} |
                                  {"collections": [{scope, provider, collection}]}},
                                  "dry_run": true, "vacuum": true}`` -- a DRY RUN unless
                                  ``dry_run`` is explicitly false.
``GET  /api/admin/db/retention``  ``{"retention": {area: days}, "last_run": {...}|null}``
``PUT  /api/admin/db/retention``  ``{"retention": {area: days|null}}`` (null clears one)
``GET  /api/admin/db/attr-shares?scope=&provider=&collection=&target_scope=``
                                  the attribute-index shares (migration 039), each filter optional
``POST /api/admin/db/attr-shares``  ``{scope, provider, collection, target_scope}`` -- make that
                                  partition searchable from ``target_scope`` too; idempotent
``DELETE /api/admin/db/attr-shares``  the same body -- stop sharing it there
``GET/POST/DELETE /api/admin/db/attr-provider-shares``  the same for a whole (scope, provider)
                                  group, ``{scope, provider, target_scope}`` (migration 040): it
                                  covers collections a later sweep adds; removing it leaves the
                                  per-collection shares
``POST /api/admin/db/attr-fold``  ``{provider, keep_scope: "shared", drop_scope, collections?,
                                  dry_run}`` -- replace ``drop_scope``'s copies of a provider's
                                  collections the shared scope holds too with shares of the shared
                                  ones; a DRY RUN (the plan) unless ``dry_run`` is explicitly false
``POST /api/admin/db/attr-prune-revisions``  ``{provider?, scopes?, collections?,
                                  include_narrowing?, dry_run}`` -- keep only the newest copy of
                                  each collection held in several scopes; older copies are folded
                                  when the newest is shared, skipped (unless include_narrowing)
                                  when it lies in a narrower scope; a DRY RUN unless told otherwise

Scopes in a share are storage prefixes, as the storage report prints them (``shared``,
``projects/<uuid>``, ``users/<sub>``). A share target must be one of those three kinds, and a
project must exist; the partition must exist. Sharing grants nobody anything: a search reads a
shared partition only for a caller who can reach its origin scope (``db/attribute_index.py``).

The rules -- what an area covers, what is never deleted -- live in ``db/storage_insight.py``.
The retention tick (:func:`retention_loop`, started from the app lifespan) prunes each area that
has a retention once a day; with none set it does nothing.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from ..db import storage_insight as si
from ..db import table_rows as tr
from ..scope import Scope
from .deps import require_pool

router = APIRouter()

#: How often the retention tick wakes, and how long it waits between two automatic prunes.
RETENTION_TICK_S = 3600.0
RETENTION_EVERY = timedelta(hours=24)


async def _body(request: Request) -> dict:
    body = await request.json() if await request.body() else {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="the body must be a JSON object")
    return body


@router.get("/db/storage")
async def admin_db_storage(request: Request) -> JSONResponse:
    pool = require_pool(request)
    report = await si.storage_report(pool)
    report["retention"] = await si.get_retention(pool)
    report["retention_last_run"] = await si.get_retention_last_run(pool)
    return JSONResponse(report)


@router.get("/db/tables/{table}/rows")
async def admin_db_table_rows(
    request: Request,
    table: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(tr.ROWS_DEFAULT_LIMIT, ge=1, le=tr.ROWS_MAX_LIMIT),
) -> JSONResponse:
    pool = require_pool(request)
    try:
        page = await tr.table_rows(pool, table, offset=offset, limit=limit)
    except tr.TableNotFound as exc:
        raise HTTPException(status_code=404, detail=f"no table {table!r} in the storage view") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        detail = tr.page_error(exc)
        if detail is None:
            raise
        raise HTTPException(status_code=504, detail=detail) from exc
    return JSONResponse(page)


@router.post("/db/prune")
async def admin_db_prune(request: Request, user: User = Depends(auth_module.current_user)) -> JSONResponse:
    pool = require_pool(request)
    body = await _body(request)
    try:
        areas = si.parse_prune_request(body.get("areas"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Only an explicit `false` deletes: a missing or mistyped flag is a preview.
    dry_run = body.get("dry_run") is not False
    try:
        result = await si.prune(pool, areas, dry_run=dry_run, vacuum=body.get("vacuum") is not False)
    except si.PruneBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not dry_run:
        logger.info(
            "db prune by %s: %d row(s), ~%d bytes in %s",
            user.sub,
            result["rows"],
            result["est_bytes"],
            ", ".join(areas),
        )
    return JSONResponse(result)


@router.get("/db/retention")
async def admin_db_retention(request: Request) -> JSONResponse:
    pool = require_pool(request)
    return JSONResponse({"retention": await si.get_retention(pool), "last_run": await si.get_retention_last_run(pool)})


@router.put("/db/retention")
async def admin_db_retention_set(request: Request, user: User = Depends(auth_module.current_user)) -> JSONResponse:
    pool = require_pool(request)
    body = await _body(request)
    retention = body.get("retention")
    if not isinstance(retention, dict):
        raise HTTPException(status_code=400, detail="body: {retention: {area: days|null}}")
    try:
        saved = await si.set_retention(pool, retention, updated_by=user.sub)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"retention": saved, "last_run": await si.get_retention_last_run(pool)})


# --- attribute-index shares ---------------------------------------------------------------------

#: The kinds of scope a partition may be shared into. Not a corpus: corpora are admin-only
#: regression assets, and nothing searches from one.
SHARE_TARGET_KINDS = ("shared", "project", "user")


SHARE_NAMES = ("scope", "provider", "collection", "target_scope")
#: A group share (migration 040) names no collection: it covers every one of (scope, provider).
GROUP_SHARE_NAMES = ("scope", "provider", "target_scope")


def _share_ref(body: dict, names: tuple[str, ...] = SHARE_NAMES) -> dict:
    """The strings that name a share, each required."""
    ref = {}
    for name in names:
        v = body.get(name)
        if not isinstance(v, str) or not v.strip():
            raise HTTPException(status_code=400, detail=f"{name!r} is required (a non-empty string)")
        ref[name] = v.strip()
    return ref


async def parse_share(pool, body: dict, names: tuple[str, ...] = SHARE_NAMES) -> dict:
    """``{scope, provider, collection, target_scope}`` (``names``: without ``collection`` for a
    group share) checked: both scopes well-formed prefixes, the target one a partition may be
    shared into, and -- for a project -- a project that exists (named by its uuid, as a stored
    prefix always is). 400 / 404 worded for the admin."""
    ref = _share_ref(body, names)
    try:
        Scope.from_prefix(ref["scope"])
        target = Scope.from_prefix(ref["target_scope"])
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if target.kind not in SHARE_TARGET_KINDS:
        raise HTTPException(status_code=400, detail=f"a partition cannot be shared into a {target.kind} scope")
    if ref["target_scope"] == ref["scope"]:
        raise HTTPException(status_code=400, detail="a partition is already searchable in its own scope")
    if target.kind == "project":
        try:
            uuid.UUID(target.id or "")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="a project scope is projects/<uuid>") from exc
        if not await db_module.project_exists(pool, target.id):
            raise HTTPException(status_code=404, detail=f"no project {target.id!r}")
    return ref


@router.get("/db/attr-shares")
async def admin_db_attr_shares(
    request: Request,
    scope: str | None = None,
    provider: str | None = None,
    collection: str | None = None,
    target_scope: str | None = None,
) -> JSONResponse:
    pool = require_pool(request)
    shares = await db_module.list_attr_shares(
        pool,
        scope=scope or None,
        provider=provider or None,
        collection=collection or None,
        target_scope=target_scope or None,
    )
    return JSONResponse({"shares": shares})


@router.post("/db/attr-shares")
async def admin_db_attr_share(request: Request, user: User = Depends(auth_module.current_user)) -> JSONResponse:
    pool = require_pool(request)
    ref = await parse_share(pool, await _body(request))
    share = await db_module.share_attr_collection(pool, **ref, shared_by=user.sub)
    if share is None:
        raise HTTPException(
            status_code=404,
            detail=f"no indexed collection {ref['provider']}/{ref['collection']} in scope {ref['scope']!r}",
        )
    logger.info(
        "attr share by %s: %s %s/%s -> %s",
        user.sub,
        ref["scope"],
        ref["provider"],
        ref["collection"],
        ref["target_scope"],
    )
    return JSONResponse({"share": share})


@router.delete("/db/attr-shares")
async def admin_db_attr_unshare(request: Request, user: User = Depends(auth_module.current_user)) -> JSONResponse:
    pool = require_pool(request)
    # No shape check beyond the four names: removing a share that could never have been made is a no-op.
    ref = _share_ref(await _body(request))
    removed = await db_module.unshare_attr_collection(pool, **ref)
    if removed:
        logger.info(
            "attr unshare by %s: %s %s/%s -> %s",
            user.sub,
            ref["scope"],
            ref["provider"],
            ref["collection"],
            ref["target_scope"],
        )
    return JSONResponse({"removed": removed})


@router.get("/db/attr-provider-shares")
async def admin_db_attr_provider_shares(
    request: Request,
    scope: str | None = None,
    provider: str | None = None,
    target_scope: str | None = None,
) -> JSONResponse:
    pool = require_pool(request)
    shares = await db_module.list_attr_provider_shares(
        pool, scope=scope or None, provider=provider or None, target_scope=target_scope or None
    )
    return JSONResponse({"shares": shares})


@router.post("/db/attr-provider-shares")
async def admin_db_attr_provider_share(
    request: Request, user: User = Depends(auth_module.current_user)
) -> JSONResponse:
    pool = require_pool(request)
    ref = await parse_share(pool, await _body(request), GROUP_SHARE_NAMES)
    share = await db_module.share_attr_provider(pool, **ref, shared_by=user.sub)
    if share is None:
        raise HTTPException(
            status_code=404, detail=f"no indexed collection of {ref['provider']!r} in scope {ref['scope']!r}"
        )
    logger.info("attr group share by %s: %s %s/* -> %s", user.sub, ref["scope"], ref["provider"], ref["target_scope"])
    return JSONResponse({"share": share})


@router.delete("/db/attr-provider-shares")
async def admin_db_attr_provider_unshare(
    request: Request, user: User = Depends(auth_module.current_user)
) -> JSONResponse:
    pool = require_pool(request)
    ref = _share_ref(await _body(request), GROUP_SHARE_NAMES)
    removed = await db_module.unshare_attr_provider(pool, **ref)
    if removed:
        logger.info(
            "attr group unshare by %s: %s %s/* -> %s", user.sub, ref["scope"], ref["provider"], ref["target_scope"]
        )
    return JSONResponse({"removed": removed})


def parse_fold(body: dict) -> dict:
    """``{provider, keep_scope, drop_scope, collections?, dry_run?}`` checked (400s). Only an
    explicit ``dry_run: false`` folds, as with a prune. Whether the fold is allowed at all (the
    kept copy must be the shared scope's) is the database layer's rule."""
    out: dict = {}
    for name in ("provider", "keep_scope", "drop_scope"):
        v = body.get(name)
        if not isinstance(v, str) or not v.strip():
            raise HTTPException(status_code=400, detail=f"{name!r} is required (a non-empty string)")
        out[name] = v.strip()
    for name in ("keep_scope", "drop_scope"):
        try:
            Scope.from_prefix(out[name])
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    collections = body.get("collections")
    if collections is not None and (
        not isinstance(collections, list) or not all(isinstance(c, str) and c.strip() for c in collections)
    ):
        raise HTTPException(status_code=400, detail="'collections' must be a list of collection names, or omitted")
    out["collections"] = [c.strip() for c in collections] if collections is not None else None
    out["dry_run"] = body.get("dry_run") is not False
    return out


@router.post("/db/attr-fold")
async def admin_db_attr_fold(request: Request, user: User = Depends(auth_module.current_user)) -> JSONResponse:
    """Keep the shared scope's copy of each collection a provider has indexed in both scopes, and
    replace ``drop_scope``'s with a share of it (``db/attribute_index.py``, ``fold_attr_duplicates``).
    A dry run unless ``dry_run`` is explicitly false."""
    pool = require_pool(request)
    spec = parse_fold(await _body(request))
    try:
        result = await db_module.fold_attr_duplicates(pool, **spec, folded_by=user.sub)
    except db_module.FoldRefused as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not spec["dry_run"]:
        logger.info(
            "attr fold by %s: %s %s -> %s, %d collection(s), %d bytes freed%s",
            user.sub,
            spec["provider"],
            spec["drop_scope"],
            spec["keep_scope"],
            result["folded"],
            result["bytes"],
            f", {len(result['errors'])} error(s)" if result["errors"] else "",
        )
    return JSONResponse(result)


def parse_prune_revisions(body: dict) -> dict:
    """``{provider?, scopes?: [a, b, ...], collections?, include_narrowing?, dry_run?}`` checked
    (400s). Only an explicit ``dry_run: false`` drops, and only an explicit
    ``include_narrowing: true`` drops a copy whose newest sibling lies in a narrower scope."""
    out: dict = {}
    provider = body.get("provider")
    if provider is not None and (not isinstance(provider, str) or not provider.strip()):
        raise HTTPException(status_code=400, detail="'provider' must be a non-empty string, or omitted")
    out["provider"] = provider.strip() if provider else None
    for name in ("scopes", "collections"):
        v = body.get(name)
        if v is not None and (not isinstance(v, list) or not all(isinstance(x, str) and x.strip() for x in v)):
            raise HTTPException(status_code=400, detail=f"{name!r} must be a list of names, or omitted")
        out[name] = [x.strip() for x in v] if v is not None else None
    if out["scopes"] is not None:
        if len(set(out["scopes"])) < 2:
            raise HTTPException(status_code=400, detail="'scopes' names at least two scopes (the copies compared)")
        for s in out["scopes"]:
            try:
                Scope.from_prefix(s)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
    out["include_narrowing"] = body.get("include_narrowing") is True
    out["dry_run"] = body.get("dry_run") is not False
    return out


@router.post("/db/attr-prune-revisions")
async def admin_db_attr_prune_revisions(
    request: Request, user: User = Depends(auth_module.current_user)
) -> JSONResponse:
    """Keep only the newest copy of each collection held in more than one scope
    (``db/attribute_index.py``, ``prune_attr_revisions``). A dry run unless ``dry_run`` is false."""
    pool = require_pool(request)
    spec = parse_prune_revisions(await _body(request))
    result = await db_module.prune_attr_revisions(pool, **spec, pruned_by=user.sub)
    if not spec["dry_run"]:
        logger.info(
            "attr prune-revisions by %s: provider %s, scopes %s, %d copy(ies) dropped, %d skipped, %d bytes freed%s%s",
            user.sub,
            spec["provider"] or "*",
            ",".join(spec["scopes"]) if spec["scopes"] else "*",
            result["dropped"],
            result["skipped"],
            result["bytes"],
            ", narrowing included" if spec["include_narrowing"] else "",
            f", {len(result['errors'])} error(s)" if result["errors"] else "",
        )
    return JSONResponse(result)


# --- the daily tick -----------------------------------------------------------------------------


def retention_due(last_run: dict | None, now: datetime) -> bool:
    """Whether the automatic prune should run: never ran, or last ran a day or more ago."""
    try:
        at = datetime.fromisoformat(str((last_run or {}).get("at")))
    except ValueError:
        return True
    if at.tzinfo is None:
        at = at.replace(tzinfo=timezone.utc)
    return now - at >= RETENTION_EVERY


async def retention_tick(pool, *, now: datetime | None = None) -> dict | None:
    """One tick: prune every area that has a retention, if a day has passed. Returns the prune's
    result, or None when nothing was due or nothing is set."""
    now = now or datetime.now(timezone.utc)
    retention = await si.get_retention(pool)
    if not retention or not retention_due(await si.get_retention_last_run(pool), now):
        return None
    areas = {area: {"older_than_days": days} for area, days in retention.items()}
    try:
        result = await si.prune(pool, areas, dry_run=False, vacuum=True, now=now)
    except si.PruneBusy:
        return None  # another replica (or an admin) is pruning; the next tick looks again
    summary = {
        "areas": {a["id"]: {"rows": a["rows"], "est_bytes": a["est_bytes"]} for a in result["areas"]},
        "rows": result["rows"],
        "est_bytes": result["est_bytes"],
        "errors": [e for a in result["areas"] for e in a.get("errors", [])] + result["vacuum"]["errors"],
    }
    await si.set_retention_last_run(pool, {"at": now.isoformat(), "result": summary})
    if result["rows"]:
        logger.info("db retention: pruned %d row(s), ~%d bytes", result["rows"], result["est_bytes"])
    return result


async def retention_loop(pool) -> None:
    """Background task (app lifespan): :func:`retention_tick` every hour. Idle unless an admin set
    a retention. Defensive per tick; only ``CancelledError`` exits."""
    logger.info("db retention: starting (checks every %ss)", RETENTION_TICK_S)
    try:
        while True:
            await asyncio.sleep(RETENTION_TICK_S)
            try:
                await retention_tick(pool)
            except Exception:
                logger.exception("db retention: tick failed")
    except asyncio.CancelledError:
        logger.info("db retention: stopped")
        raise
