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

The rules -- what an area covers, what is never deleted -- live in ``db/storage_insight.py``.
The retention tick (:func:`retention_loop`, started from the app lifespan) prunes each area that
has a retention once a day; with none set it does nothing.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import auth as auth_module
from ..auth import User
from ..db import storage_insight as si
from ..db import table_rows as tr
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
