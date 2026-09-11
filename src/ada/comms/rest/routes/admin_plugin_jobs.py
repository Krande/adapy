"""Admin plugin-job schedule routes ("cron for plugin jobs"):
``GET``/``POST /api/admin/plugin-jobs/schedules``, ``PATCH``/``DELETE
.../{schedule_id}``, ``POST .../{schedule_id}/run``.

:func:`plugin_schedule_fire` is also called from the background
``_plugin_schedule_loop`` (still inside ``create_app`` — the tick that
claims due schedules every 30s has not been extracted), which is why it
takes :class:`~.deps.RestContext` explicitly rather than closing over the
app's queue: ``create_app`` keeps calling it as ``_plugin_schedule_fire``,
bound to its own ``RestContext``, until that loop moves too. Same shape as
``routes/plugin_jobs.py``'s ``enqueue_plugin_job``.

Extracted from ``create_app``; see ``routes/__init__`` for the pattern.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import auth as auth_module
from .. import db as db_module
from ..auth import User
from .deps import (
    RestContext,
    SystemUser,
    live_worker_specs,
    next_fire,
    parse_scope,
    require_pool,
    resolve_project_scope,
    rest_context,
    validate_cron,
)
from .plugin_jobs import enqueue_plugin_job

router = APIRouter()

#: Options key the tick stamps with each firing's timestamp.
#:
#: ALWAYS, AND THERE IS NO WAY TO TURN IT OFF. Core hashes a plugin job's
#: options into its source key so identical requests cache-hit, which is
#: right for a user pressing a button twice and catastrophic for a schedule:
#: byte-identical options every hour means the second firing and every one
#: after returns the FIRST run's summary. Hourly green ticks, the worker never
#: touched, and a caller trusting data that stopped moving.
#:
#: Read by nobody. It exists to change the hash, exactly as `refresh` does on
#: the plugin-job read path -- and it is stamped by the TICK rather than
#: offered as a schedule field, because a scheduled run that legitimately
#: answers "same as last time" cannot be told apart from one that never
#: happened.
SCHEDULE_FIRE_TOKEN = "scheduled_at"


def plugin_schedule_options(schedule_row: dict, fired_at) -> dict:
    """The options to dispatch: the schedule's own, plus the fire token."""
    options = dict(schedule_row.get("options") or {})
    options[SCHEDULE_FIRE_TOKEN] = fired_at.isoformat()
    return options


@router.get("/plugin-jobs/schedules")
async def admin_plugin_job_schedules_list(request: Request) -> JSONResponse:
    pool = require_pool(request)
    include_archived = request.query_params.get("include_archived") in ("1", "true", "yes")
    rows = await db_module.list_plugin_job_schedules(pool, include_archived=include_archived)
    return JSONResponse({"schedules": rows})


@router.post("/plugin-jobs/schedules")
async def admin_plugin_job_schedules_create(
    request: Request,
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Create a schedule.

    Body: ``{"name", "cron_expr", "scope", "plugin_id", "options",
    "capability", "enabled"}``.

    ``plugin_id`` is NOT checked against the live plugin registry. A schedule
    may legitimately be created before the worker that serves it exists, or
    survive a pool being down for a day; refusing here would make the admin
    panel depend on a worker being up to configure anything. An id nothing
    serves shows up as a queued job that nobody picks up, which is already
    visible in /my-jobs.
    """
    pool = require_pool(request)
    body = await request.json() if await request.body() else {}
    name = (body.get("name") or "").strip()
    cron_expr = validate_cron(body.get("cron_expr") or "")
    scope_str = (body.get("scope") or "").strip()
    plugin_id = (body.get("plugin_id") or "").strip()
    options = body.get("options") or {}
    capability = (body.get("capability") or "").strip() or None
    enabled = bool(body.get("enabled", True))

    if not name:
        raise HTTPException(status_code=400, detail="name required")
    if not scope_str:
        raise HTTPException(status_code=400, detail="scope required")
    if not plugin_id:
        raise HTTPException(status_code=400, detail="plugin_id required")
    if not isinstance(options, dict):
        raise HTTPException(status_code=400, detail="options must be an object")
    if SCHEDULE_FIRE_TOKEN in options:
        # Refused rather than silently overwritten: a caller who set it
        # believes it means something, and the tick is about to replace it.
        raise HTTPException(
            status_code=400,
            detail=(
                f"options.{SCHEDULE_FIRE_TOKEN} is reserved — the scheduler stamps it on every "
                "firing so that two firings never share an options hash and cache-hit"
            ),
        )
    # Parses only. Slug-to-id resolution happens at fire time so renaming a
    # project does not strand a schedule.
    _ = parse_scope(scope_str, user)

    try:
        row = await db_module.create_plugin_job_schedule(
            pool,
            name=name,
            cron_expr=cron_expr,
            scope=scope_str,
            plugin_id=plugin_id,
            options=options,
            capability=capability,
            enabled=enabled,
            next_fire_at=next_fire(cron_expr),
            created_by=user.sub,
        )
    except Exception as exc:
        if exc.__class__.__name__ == "UniqueViolationError":
            raise HTTPException(status_code=409, detail=f"schedule name {name!r} already in use") from exc
        raise
    return JSONResponse(row, status_code=201)


@router.patch("/plugin-jobs/schedules/{schedule_id}")
async def admin_plugin_job_schedules_update(
    schedule_id: str,
    request: Request,
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Partial update. Only the fields present in the body move."""
    pool = require_pool(request)
    body = await request.json() if await request.body() else {}
    fields: dict = {}

    if "name" in body:
        name = (body.get("name") or "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="name cannot be empty")
        fields["name"] = name
    if "cron_expr" in body:
        fields["cron_expr"] = validate_cron(body.get("cron_expr") or "")
        # A changed expression makes the stored next_fire_at meaningless, so
        # it is recomputed here rather than left to drift until the next fire.
        fields["next_fire_at"] = next_fire(fields["cron_expr"])
    if "scope" in body:
        scope_str = (body.get("scope") or "").strip()
        if not scope_str:
            raise HTTPException(status_code=400, detail="scope cannot be empty")
        _ = parse_scope(scope_str, user)
        fields["scope"] = scope_str
    if "plugin_id" in body:
        plugin_id = (body.get("plugin_id") or "").strip()
        if not plugin_id:
            raise HTTPException(status_code=400, detail="plugin_id cannot be empty")
        fields["plugin_id"] = plugin_id
    if "options" in body:
        options = body.get("options") or {}
        if not isinstance(options, dict):
            raise HTTPException(status_code=400, detail="options must be an object")
        if SCHEDULE_FIRE_TOKEN in options:
            raise HTTPException(
                status_code=400,
                detail=f"options.{SCHEDULE_FIRE_TOKEN} is reserved — the scheduler stamps it",
            )
        fields["options"] = options
    if "capability" in body:
        fields["capability"] = (body.get("capability") or "").strip() or None
    if "enabled" in body:
        fields["enabled"] = bool(body.get("enabled"))
        # Re-enabling a schedule whose next_fire_at is long past would fire
        # immediately and then again on its real slot. Recomputed from now.
        if fields["enabled"]:
            current = await db_module.get_plugin_job_schedule(pool, schedule_id)
            if current is None:
                raise HTTPException(status_code=404, detail="schedule not found")
            fields.setdefault("next_fire_at", next_fire(current["cron_expr"]))

    row = await db_module.update_plugin_job_schedule(pool, schedule_id, **fields)
    if row is None:
        raise HTTPException(status_code=404, detail="schedule not found")
    return JSONResponse(row)


@router.delete("/plugin-jobs/schedules/{schedule_id}")
async def admin_plugin_job_schedules_archive(schedule_id: str, request: Request) -> JSONResponse:
    pool = require_pool(request)
    if not await db_module.archive_plugin_job_schedule(pool, schedule_id):
        raise HTTPException(status_code=404, detail="schedule not found, or already archived")
    return JSONResponse({"archived": schedule_id})


@router.post("/plugin-jobs/schedules/{schedule_id}/run")
async def admin_plugin_job_schedules_run_now(
    schedule_id: str,
    request: Request,
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Fire a schedule immediately, without waiting for its slot.

    The reason this exists is that a schedule is otherwise unverifiable: an
    admin who has just created one has no way to learn whether its options,
    scope and plugin actually produce a job short of waiting for the cron to
    come round. It does not disturb the timetable -- ``next_fire_at`` is left
    alone, so the scheduled slot still happens.
    """
    pool = require_pool(request)
    row = await db_module.get_plugin_job_schedule(pool, schedule_id)
    if row is None:
        raise HTTPException(status_code=404, detail="schedule not found")

    outcome = await plugin_schedule_fire(ctx, pool, row, fired_at=datetime.now(timezone.utc))
    if outcome.get("skipped"):
        # 409: the request was valid, the state said no. The reason is the
        # useful half.
        raise HTTPException(status_code=409, detail=outcome["skipped"])
    return JSONResponse(outcome)


async def plugin_schedule_fire(ctx: RestContext, pool, schedule_row: dict, *, fired_at) -> dict:
    """Enqueue one schedule's job. Returns what happened.

    ``{"job_id": ..., "schedule": ...}`` on a firing, or
    ``{"skipped": "<reason>"}`` when state said no. Every skip is also written
    to the row, because a schedule that silently does nothing is the failure
    this whole feature exists to remove.
    """
    queue = ctx.queue
    sched_id = schedule_row["id"]
    plugin_id = schedule_row["plugin_id"]

    try:
        scope_obj = parse_scope(schedule_row["scope"], SystemUser())
        scope_obj = await resolve_project_scope(pool, scope_obj)
    except HTTPException as exc:
        reason = f"scope {schedule_row['scope']!r} did not resolve ({exc.status_code}): {exc.detail}"
        await db_module.set_plugin_job_schedule_skip_reason(pool, sched_id, reason)
        return {"skipped": reason}
    except Exception as exc:
        logger.exception("plugin-job scheduler: scope resolution crashed for %s", sched_id)
        reason = f"scope resolution crashed: {exc}"
        await db_module.set_plugin_job_schedule_skip_reason(pool, sched_id, reason)
        return {"skipped": reason}

    # Concurrent-fire guard. A plugin job can hold a single licensed
    # workstation for minutes, so an overlapping firing does not just double
    # the load -- the two contend for one resource and the loser fails in a
    # way that reads as the plugin's fault. The missed slot is NOT backfired:
    # the next slot is the next chance, which is the same choice the audit
    # scheduler makes and for the same reason.
    try:
        candidates = await db_module.plugin_job_in_flight_jobs(
            pool,
            scope_kind=scope_obj.kind,
            scope_id=scope_obj.id,
            plugin_id=plugin_id,
        )
        # THE AUDIT ROW IS NOT THE ANSWER ON ITS OWN. Its terminal status is
        # written by the worker, so a worker with no database pool never writes
        # one -- it announces that at startup -- and in such a deployment every
        # plugin job stays `queued` in the log forever. Trusting the row alone
        # would let a schedule fire exactly ONCE and then block itself for good,
        # which is a worse failure than the double-firing the guard prevents.
        #
        # So each candidate is checked against the queue, which the worker DOES
        # update. A missing entry is decisive too: it means the job is gone from
        # the queue entirely, so nothing is going to run it whatever the row says.
        in_flight = None
        for candidate in candidates:
            entry = await queue.get(candidate) if queue.enabled else None
            if entry is None:
                continue
            if str(getattr(entry, "status", "") or "").lower() in ("queued", "running"):
                in_flight = candidate
                break
    except Exception:
        logger.exception("plugin-job scheduler: concurrent-fire check failed for %s", sched_id)
        # Not firing is the safe half of an unknown: a duplicate run on a
        # single-seat resource is worse than a missed slot.
        reason = "could not check whether a previous job is still running; slot skipped"
        await db_module.set_plugin_job_schedule_skip_reason(pool, sched_id, reason)
        return {"skipped": reason}
    if in_flight is not None:
        reason = f"previous {plugin_id} job {in_flight} still queued or running"
        await db_module.set_plugin_job_schedule_skip_reason(pool, sched_id, reason)
        return {"skipped": reason}

    # ROUTING IS RESOLVED HERE, AND A SLOT THAT CANNOT ROUTE IS SKIPPED.
    #
    # A plugin job's pool comes from the plugin's LIVE spec, which exists only
    # while a worker advertising it is online. Enqueuing anyway is not a
    # smaller failure than skipping: with no spec there is no capability, and
    # a job with no capability goes to the default pool, which no specialised
    # worker subscribes to. Nothing ever pulls it -- so it is not retried, and
    # it never reaches the delivery-attempt cap that would mark it failed. It
    # sits at "queued" for good, with nothing in any worker's log to explain
    # it, which is the single worst outcome available here.
    #
    # A missed slot is recoverable and says so; the next slot is the next
    # chance, and `last_skipped_reason` names what was wrong. This matters most
    # during a worker restart, which is exactly when an unattended schedule is
    # most likely to fire into an empty fleet.
    #
    # An explicitly configured capability is trusted and fires regardless: the
    # admin named the pool, and a pool may be legitimately empty for a while.
    capability = (schedule_row.get("capability") or "").strip() or None
    plugin_spec = None
    if capability is None:
        for _spec in (await live_worker_specs(queue, "plugin_specs")).values():
            if _spec.get("slug") == plugin_id or _spec.get("id") == plugin_id:
                plugin_spec = _spec
                break
        if plugin_spec is None:
            reason = (
                f"no online worker advertises {plugin_id!r}, so the job could not be routed to a "
                f"pool; slot skipped rather than queued where nothing would ever pull it"
            )
            await db_module.set_plugin_job_schedule_skip_reason(pool, sched_id, reason)
            return {"skipped": reason}

    options = plugin_schedule_options(schedule_row, fired_at)
    try:
        job_id = await enqueue_plugin_job(
            ctx,
            plugin_id=plugin_id,
            options=options,
            scope_obj=scope_obj,
            capability=capability,
            user=SystemUser(),
            pool=pool,
            plugin_spec=plugin_spec,
        )
    except HTTPException as exc:
        reason = f"enqueue refused ({exc.status_code}): {exc.detail}"
        await db_module.set_plugin_job_schedule_skip_reason(pool, sched_id, reason)
        return {"skipped": reason}
    except Exception as exc:
        logger.exception("plugin-job scheduler: enqueue crashed for %s", sched_id)
        reason = f"enqueue crashed: {exc}"
        await db_module.set_plugin_job_schedule_skip_reason(pool, sched_id, reason)
        return {"skipped": reason}

    # CLEARED ON SUCCESS, not only on claim. The tick's claim clears it, but
    # "Run now" calls this function directly and bypasses the claim -- so a
    # schedule that skipped once and then fired successfully kept displaying the
    # old skip note indefinitely, which reads as the current state and sent
    # someone looking for a queued job that had finished long before.
    #
    # Written here rather than at each call site because every successful
    # firing, however it was triggered, makes the previous skip history.
    await db_module.update_plugin_job_schedule(pool, sched_id, last_job_id=job_id, last_skipped_reason=None)
    logger.info(
        "plugin-job scheduler: fired %s (%s) -> job %s",
        schedule_row["name"],
        plugin_id,
        job_id,
    )
    return {"job_id": job_id, "schedule": schedule_row["name"], "plugin_id": plugin_id}
