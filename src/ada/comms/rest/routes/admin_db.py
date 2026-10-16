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


def _share_ref(body: dict) -> dict:
    """The four strings that name a share, each required."""
    ref = {}
    for name in ("scope", "provider", "collection", "target_scope"):
        v = body.get(name)
        if not isinstance(v, str) or not v.strip():
            raise HTTPException(status_code=400, detail=f"{name!r} is required (a non-empty string)")
        ref[name] = v.strip()
    return ref


async def parse_share(pool, body: dict) -> dict:
    """``{scope, provider, collection, target_scope}`` checked: both scopes well-formed prefixes,
    the target one a partition may be shared into, and -- for a project -- a project that exists
    (named by its uuid, as a stored prefix always is). 400 / 404 worded for the admin."""
    ref = _share_ref(body)
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
