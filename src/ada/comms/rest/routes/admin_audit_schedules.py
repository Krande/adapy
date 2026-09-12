"""Admin audit-schedule routes (M4 admin audit panel):

    GET    /admin/audit/schedules            list live schedules
    POST   /admin/audit/schedules            create a schedule
    PATCH  /admin/audit/schedules/{id}       partial update
    DELETE /admin/audit/schedules/{id}       soft-archive
    POST   /admin/audit/schedules/{id}/fire  fire-now (bypasses cron)

The actual firing happens via the scheduler background task
(``_scheduler_loop``, still inside ``create_app``). These endpoints just
CRUD the rows + offer the manual "fire this schedule right now" override,
via :func:`~.admin_audit_runs.audit_dispatch` (also called from that
background task and from the audit-runs routes — see that module's
docstring for why it takes ``RestContext`` explicitly).

Extracted from ``create_app``; see ``routes/__init__`` for the pattern.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from ..scope import can_access as scope_can_access
from .admin_audit_runs import audit_dispatch
from .deps import (
    RestContext,
    next_fire,
    parse_scope,
    require_pool,
    resolve_project_scope,
    rest_context,
    validate_cron,
)

router = APIRouter()


@router.get("/audit/schedules")
async def admin_audit_schedules_list(request: Request) -> JSONResponse:
    pool = require_pool(request)
    rows = await db_module.list_audit_schedules(pool)
    return JSONResponse({"schedules": rows})


@router.post("/audit/schedules")
async def admin_audit_schedules_create(
    request: Request,
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Create a new schedule.

    Body: ``{"name": "...", "cron_expr": "0 2 * * *",
             "scope": "corpus:cad-baseline",
             "worker_pool": "audit" | null,
             "enabled": true}``.

    ``cron_expr`` is validated via croniter; the next fire instant
    is computed and stored so the scheduler tick can pick it up
    on its very next pass without re-parsing.
    """
    pool = require_pool(request)
    body = await request.json() if await request.body() else {}
    name = (body.get("name") or "").strip()
    cron_expr = validate_cron(body.get("cron_expr") or "")
    scope_str = (body.get("scope") or "").strip()
    worker_pool = body.get("worker_pool") or None
    enabled = bool(body.get("enabled", True))
    if not name:
        raise HTTPException(status_code=400, detail="name required")
    if not scope_str:
        raise HTTPException(status_code=400, detail="scope required")
    # Validate scope-string parses (raises 400 with detail). Don't
    # resolve project slugs to ids yet — slug→id resolution
    # happens at fire time so renaming a project doesn't strand
    # a schedule.
    _ = parse_scope(scope_str, user)
    computed_next_fire = next_fire(cron_expr)
    try:
        row = await db_module.create_audit_schedule(
            pool,
            name=name,
            cron_expr=cron_expr,
            scope=scope_str,
            worker_pool=worker_pool,
            next_fire_at=computed_next_fire,
            enabled=enabled,
            created_by=user.sub,
        )
    except Exception as exc:
        if exc.__class__.__name__ == "UniqueViolationError":
            raise HTTPException(
                status_code=409,
                detail=f"schedule name {name!r} already in use",
            ) from exc
        raise
    return JSONResponse(row, status_code=201)


@router.patch("/audit/schedules/{schedule_id}")
async def admin_audit_schedules_update(
    schedule_id: str,
    request: Request,
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Partial update. Recognised fields: ``name``, ``cron_expr``,
    ``scope``, ``worker_pool``, ``enabled``. Editing ``cron_expr``
    recomputes ``next_fire_at`` from the current instant so a
    retimed schedule fires from the new pattern immediately
    instead of waiting for the old slot."""
    pool = require_pool(request)
    body = await request.json() if await request.body() else {}
    kwargs: dict = {}
    if "name" in body:
        val = (body.get("name") or "").strip()
        if not val:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        kwargs["name"] = val
    new_cron: str | None = None
    if "cron_expr" in body:
        new_cron = validate_cron(body.get("cron_expr") or "")
        kwargs["cron_expr"] = new_cron
    if "scope" in body:
        val = (body.get("scope") or "").strip()
        if not val:
            raise HTTPException(status_code=400, detail="scope cannot be empty")
        _ = parse_scope(val, user)
        kwargs["scope"] = val
    if "worker_pool" in body:
        kwargs["worker_pool"] = body.get("worker_pool") or None
        kwargs["worker_pool_set"] = True
    if "enabled" in body:
        kwargs["enabled"] = bool(body["enabled"])
    if new_cron is not None:
        kwargs["next_fire_at"] = next_fire(new_cron)
        kwargs["next_fire_at_set"] = True
    try:
        row = await db_module.update_audit_schedule(pool, schedule_id, **kwargs)
    except Exception as exc:
        if exc.__class__.__name__ == "UniqueViolationError":
            raise HTTPException(
                status_code=409,
                detail="schedule name already in use",
            ) from exc
        raise
    if row is None:
        raise HTTPException(status_code=404, detail="schedule not found")
    return JSONResponse(row)


@router.delete("/audit/schedules/{schedule_id}")
async def admin_audit_schedules_archive(
    schedule_id: str,
    request: Request,
) -> JSONResponse:
    pool = require_pool(request)
    ok = await db_module.archive_audit_schedule(pool, schedule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="schedule not found")
    return JSONResponse({"id": schedule_id, "archived": True})


@router.post("/audit/schedules/{schedule_id}/fire")
async def admin_audit_schedules_fire_now(
    schedule_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Manual "fire now" — dispatch the schedule's scope without
    waiting for the next cron slot. Honours the concurrent-fire
    guard so a "fire now" while another run is still in flight
    returns 409 instead of stacking workloads.

    Does NOT advance ``next_fire_at`` — the next scheduled slot
    still fires as planned. Useful for testing a freshly-created
    schedule or for backfilling after fixing a broken corpus.
    """
    ctx.jobs.require("conversion")
    pool = require_pool(request)
    row = await db_module.get_audit_schedule(pool, schedule_id)
    if row is None or row["archived_at"] is not None:
        raise HTTPException(status_code=404, detail="schedule not found")
    scope_str = row["scope"]
    worker_pool = row["worker_pool"]
    s = parse_scope(scope_str, user)
    s = await resolve_project_scope(pool, s)
    if not await scope_can_access(user, s, pool):
        raise HTTPException(status_code=403, detail="forbidden")
    if await db_module.audit_run_exists_for_key(pool, scope_str, worker_pool):
        raise HTTPException(
            status_code=409,
            detail="another audit run with this (scope, pool) is still running",
        )
    run = await db_module.create_audit_run(
        pool,
        scope=scope_str,
        worker_pool=worker_pool,
        trigger="manual",  # operator-initiated even though it's a schedule
        note=f"fire-now: {row['name']}",
        created_by=user.sub,
    )
    background_tasks.add_task(
        audit_dispatch,
        ctx,
        run["id"],
        s,
        worker_pool,
        user.sub,
        pool,
    )
    return JSONResponse(run, status_code=202)
