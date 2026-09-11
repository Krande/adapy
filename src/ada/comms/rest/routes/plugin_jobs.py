"""Plugin job + job-status/cancel routes: ``POST /api/plugins/{plugin_id}/jobs``,
``POST /api/jobs/{job_id}/status``, and the per-user in-flight job list/cancel
(``GET``/``POST /api/scopes/{scope}/my-jobs...``).

:func:`enqueue_plugin_job` is the ONE enqueue path shared with the plugin-job
scheduler's tick (``admin.post("/plugin-jobs/schedules/{schedule_id}/run")``,
still inside ``create_app`` — the admin routes have not been extracted yet).
That is why it lives here as a plain function taking :class:`~.deps.RestContext`
explicitly rather than as a route-local closure: ``create_app`` keeps calling
it as ``_enqueue_plugin_job``, bound to its own ``RestContext``, until the
scheduler routes move too.

Extracted from ``create_app``; see ``routes/__init__`` for the pattern.
"""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import auth as auth_module
from .. import db as db_module
from .. import local_jobs, pending_uploads
from ..auth import User
from ..plugin_registry import locally_registered_spec
from ..queue import capability_token
from ..scope import Scope
from ..scope import can_access as scope_can_access
from .deps import (
    RestContext,
    live_worker_specs,
    parse_scope,
    pending_upload_detail,
    require_pool,
    resolve_project_scope,
    rest_context,
    scope_from_path,
)
from .plugins import plugin_ids_gated_by_config

router = APIRouter()

# A terminal (or in-flight) status a worker/local job may self-report via
# ``POST /jobs/{job_id}/status``. Not ``queued`` — that is the state the
# enqueue itself already wrote; a report can only move it forward.
_REPORTABLE_JOB_STATUSES = ("running", "done", "error", "cancelled")


async def plugin_job_requires_admin(plugin_id: str, pool, advertised: dict | None) -> bool:
    """Whether enqueuing ``plugin_id``'s job requires an admin.

    OR across every source — see ``routes.plugins.PLUGIN_JOB_ADMIN_SETTING``.
    Any one of them restricting is enough, so adding a source can only tighten.
    """
    if bool((advertised or {}).get("requires_admin")):
        return True
    if bool((locally_registered_spec(plugin_id) or {}).get("requires_admin")):
        return True
    gated = await plugin_ids_gated_by_config(pool)
    if gated is None:  # unreadable setting -> gate everything
        return True
    return plugin_id in gated


async def enqueue_plugin_job(
    ctx: RestContext,
    *,
    plugin_id: str,
    options: dict,
    scope_obj: Scope,
    capability: str | None = None,
    user,
    pool=None,
    request: Request | None = None,
    derived_prefix: str | None = None,
    derived_key: str | None = None,
    plugin_spec: dict | None = None,
) -> str:
    """Enqueue one plugin job and return its job id.

    ONE ENQUEUE PATH, two callers: ``POST /plugins/{id}/jobs`` and the
    plugin-job scheduler's tick. That is not tidiness -- it is the property
    that makes a scheduled firing indistinguishable from a user-initiated one
    to the worker, so capability routing, audit rows, cancellation and the
    cached-blob short circuit cannot drift between them. A second enqueue for
    the scheduler would be a second set of those behaviours to keep in step.

    AUTHORISATION IS THE CALLER'S. This helper does not check scope access or
    the plugin's admin gate: the route does both before calling, and the tick
    runs as the system identity by construction. Putting the checks here would
    read as safer and would in fact be looser, because the tick would then be
    passing a synthetic admin through a gate built for real users.
    """
    # Synthetic source_key over (plugin_id, options hash) so identical
    # requests cache-hit. A SCHEDULE therefore has to vary its options, which
    # is what SCHEDULE_FIRE_TOKEN is for -- see the note on it.
    opts_hash = hashlib.sha256(json.dumps(options, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    source_key = f"_synthetic/plugin_job/{plugin_id}/{opts_hash}"
    if not isinstance(derived_key, str) or not derived_key.strip():
        derived_key = f"_derived/plugin_jobs/{plugin_id}/{opts_hash}.json"

    if plugin_spec is None:
        for _spec in (await live_worker_specs(ctx.queue, "plugin_specs")).values():
            if _spec.get("slug") == plugin_id or _spec.get("id") == plugin_id:
                plugin_spec = _spec
                break

    # Route to the pool advertising this plugin's worker_capability. An
    # explicit override wins; otherwise read it off the live spec.
    target_capability = None
    if isinstance(capability, str) and capability.strip():
        target_capability = capability_token(capability)
    elif plugin_spec is not None:
        cap = plugin_spec.get("worker_capability")
        if isinstance(cap, str) and cap.strip():
            target_capability = capability_token(cap)
        # SHARDED POOLS: a plugin may advertise `capability_option` naming one
        # of its own options; supplying it routes to `<capability>-<value>`.
        # See the long note on the route for why subject routing rather than
        # NAKing is how non-interchangeable workers are kept apart.
        opt_name = plugin_spec.get("capability_option")
        if target_capability and isinstance(opt_name, str) and opt_name:
            shard = capability_token(options.get(opt_name))
            if shard:
                target_capability = f"{target_capability}-{shard}"

    # No queue: run it here, in a thread. A single-node viewer otherwise has
    # no way to run a plugin job at all.
    if not ctx.queue.enabled:
        try:
            local = local_jobs.start_plugin_job(
                plugin_id=plugin_id,
                options=options,
                derived_prefix=derived_prefix,
                derived_key=derived_key,
                storage=ctx.storage,
                scope=scope_obj,
            )
        except LookupError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc
        return local.job_id

    job = await ctx.queue.enqueue(
        source_key,
        target_format="plugin_job",
        scope_kind=scope_obj.kind,
        scope_id=scope_obj.id,
        conversion_options={
            "plugin_id": plugin_id,
            "options": options,
            "derived_prefix": derived_prefix,
        },
        derived_key=derived_key,
        target_capability=target_capability,
        # Hold the publish until the audit row exists -- publishing first is
        # what strands a row at "queued" with no message left to recover it.
        publish=False,
    )
    await ctx.audit(
        request,
        user,
        scope_obj,
        "plugin_job",
        key=source_key,
        target_format="plugin_job",
        status="queued",
        job_id=job.job_id,
        pool=pool,
    )
    await ctx.queue.publish(job)
    return job.job_id


@router.post("/jobs/{job_id}/status")
async def api_job_status_report(
    job_id: str,
    request: Request,
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Report a job's progress into the audit log. Body: ``{status, ...}``.

    WHY THIS EXISTS. The audit row is written `queued` by the API at enqueue
    and moved by the WORKER -- through a database pool. A worker without
    ``DATABASE_URL`` is a supported deployment and announces itself as one at
    startup, and on such a worker both status hops are no-ops: the row stays
    `queued` for ever while the job runs, finishes and is swept. The queue
    record is accurate throughout, so the conversion toast follows along
    happily and the Audit tab -- the surface an operator actually audits with
    -- shows every job on that pool as permanently pending.

    That is worse than a cosmetic gap. A permanently-`queued` row is
    indistinguishable from a job nothing will ever run, so an operator cannot
    tell a healthy pool from a broken one, and anything that reasons over
    non-terminal rows (the plugin-job concurrent-fire guard, for one) blocks
    on jobs that finished minutes ago.

    Same argument, and the same answer, as ``POST
    /scopes/{scope}/source-nodes``: a worker that can already reach this API
    should not need a second and far more powerful credential to say what it
    just did.

    AUTHORISATION IS THE JOB'S SCOPE, matching ``GET /convert/{job_id}``:
    the queue record names the scope, and a caller who may read that scope's
    jobs may report on them. The write is deliberately narrow -- a status from
    a closed set plus the outcome fields the in-process path already writes --
    so the route cannot be used to edit an audit row into saying something
    else. A terminal row is never moved again, so a late or duplicated report
    cannot rewrite history.
    """
    # THE REQUEST IS VALIDATED BEFORE THE DEPLOYMENT IS CONSULTED. A malformed
    # status is wrong whether or not this deployment has a database, and
    # answering 200 "recorded: false" to it would tell a caller its typo was
    # merely unused rather than invalid.
    try:
        body = await request.json()
    except Exception:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")
    status = (str(body.get("status") or "")).strip().lower()
    if status not in _REPORTABLE_JOB_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"status must be one of {', '.join(_REPORTABLE_JOB_STATUSES)}",
        )

    pool = getattr(request.app.state, "db_pool", None)
    if pool is None:
        # No audit log to write. Not an error: the deployment has no database,
        # which is exactly the case where nobody is reading audit rows either.
        return JSONResponse({"job_id": job_id, "recorded": False, "reason": "no database configured"})

    if not ctx.queue.enabled:
        raise HTTPException(status_code=503, detail="no job queue configured")
    job = await ctx.queue.get(job_id)
    if job is None:
        # The queue entry is swept ~15 minutes after a job goes terminal, so a
        # report that arrives after that cannot be authorised against a scope
        # any more. 404 rather than a guess.
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    job_scope = (
        Scope.shared()
        if job.scope_kind == "shared"
        else Scope(kind=job.scope_kind, id=job.scope_id)  # type: ignore[arg-type]
    )
    if not await scope_can_access(user, job_scope, pool):
        raise HTTPException(status_code=403, detail="forbidden")

    def _int(name: str) -> int | None:
        value = body.get(name)
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"{name} must be an integer") from None

    def _text(name: str, limit: int) -> str | None:
        value = body.get(name)
        if value is None:
            return None
        # Truncated rather than refused: a traceback is the most useful thing
        # in a failure report and the least predictable in length, and losing
        # the whole report because the tail was long would be the wrong trade.
        return str(value)[:limit]

    if status == "running":
        await db_module.mark_audit_running(
            pool,
            job_id=job_id,
            worker_image_tag=_text("worker_image_tag", 200),
        )
        return JSONResponse({"job_id": job_id, "recorded": True, "status": status})

    await db_module.update_audit_by_job(
        pool,
        job_id=job_id,
        status=status,
        error=_text("error", 4000),
        traceback=_text("traceback", 20000),
        duration_ms=_int("duration_ms"),
        cpu_user_ms=_int("cpu_user_ms"),
        cpu_sys_ms=_int("cpu_sys_ms"),
        peak_rss_kb=_int("peak_rss_kb"),
        read_bytes=_int("read_bytes"),
        write_bytes=_int("write_bytes"),
        worker_image_tag=_text("worker_image_tag", 200),
    )
    return JSONResponse({"job_id": job_id, "recorded": True, "status": status})


@router.post("/scopes/{scope}/my-jobs/{job_id}/cancel")
async def api_scope_cancel_my_job(
    request: Request,
    job_id: str,
    ctx: RestContext = Depends(rest_context),
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Mark a queued/running conversion as cancelled.

    Who may cancel depends on which branch answers, and the two
    differ deliberately. A QUEUED job is owner-checked: the SQL
    filters on ``audit_log.user_sub``, so only the caller who
    started it can stop it. An IN-PROCESS job is scope-checked
    instead — ``LocalJob`` records no owner, and the path only runs
    when no queue is configured, which is the single-node shape
    where ``current_user`` is the one synthetic ``local-dev``
    principal and the two checks coincide. Note that a ``shared``
    scope grants every authenticated user, so on a no-queue server
    that DOES have auth enabled this is a weaker guarantee than the
    queued branch gives.

    Side effects:
      * audit_log row flipped to ``status='cancelled'`` (only if
        it was still queued or running — terminal rows are left
        alone);
      * the KV bucket entry is updated best-effort so any active
        poll loop sees the new status on its next tick.

    An IN-PROCESS plugin job is cancelled for real: its entry in the
    local registry carries a ``cancel_event``, the job entrypoint is
    already handed that event, and setting it is what lets a
    cooperative plugin stop between units of work. These jobs exist
    whether or not a queue or a database does, so they are handled
    before the pool is required — otherwise the one deployment where
    cancelling CAN work (a local server, no NATS, no DB) is the one
    where the endpoint 500s.

    For a QUEUED job the worker process still is not notified, so a
    bake that was actively mid-run continues to completion. The
    resulting derived blob lands on storage as orphaned data. For the
    toast UX this is enough — the user sees the row disappear and is
    unblocked.
    """
    local = local_jobs.registry.get(job_id)
    if local is not None:
        # Same access check the read route makes on the same job:
        # stopping someone else's work must not be easier than looking
        # at it.
        local_scope = Scope(kind=local.scope_kind, id=local.scope_id)
        if not await scope_can_access(user, local_scope, getattr(request.app.state, "db_pool", None)):
            raise HTTPException(status_code=403, detail="forbidden")
        return JSONResponse({"job_id": job_id, "cancelled": local_jobs.registry.cancel(job_id)})
    pool = require_pool(request)
    cancelled = await db_module.cancel_audit_by_job(
        pool,
        job_id=job_id,
        user_sub=user.sub,
    )
    if not cancelled:
        return JSONResponse(
            {"job_id": job_id, "cancelled": False, "reason": "not owned, missing, or already terminal"},
            status_code=404,
        )
    # Best-effort: nudge the KV bucket so /api/convert/{job_id}
    # polls immediately reflect the new status. Worker writes will
    # subsequently overwrite this back to 'running' on its next
    # progress tick — that's expected; the audit_log row is the
    # source of truth for the user-visible state.
    queue = getattr(request.app.state, "queue", None)
    if queue is not None:
        try:
            await queue.update(
                job_id,
                status="cancelled",
                error="cancelled by user",
            )
        except Exception:
            # Queue update is decorative; the audit row is what
            # the toast restore reads.
            pass
    return JSONResponse({"job_id": job_id, "cancelled": True})


@router.get("/scopes/{scope}/my-jobs")
async def api_scope_my_jobs(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    limit: int = 200,
) -> JSONResponse:
    """Conversions the calling user kicked off in this scope that
    are still in flight (``queued`` or ``running``).

    Frontend hits this on app load to repopulate the
    bottom-right ConversionProgress toast for jobs that survived
    a page reload. Scoped to (current user, current scope) so a
    user can't see anyone else's jobs and so cross-scope jobs
    don't clutter the toast when the active scope is unrelated.

    ``error`` rows are intentionally excluded — they're terminal
    and the toast's error-row UX expects manual dismissal, not a
    silent restore. Errors that happened while the user was away
    can be discovered via the (admin) audit log or a future
    per-user history view.
    """
    pool = require_pool(request)
    rows = await db_module.list_audit(
        pool,
        user_sub=user.sub,
        scope_kind=scope_obj.kind,
        scope_id=scope_obj.id,
        statuses=["queued", "running"],
        # Cap at 500 (same as the underlying list_audit clamp)
        # so a typo'd limit can't pin the DB. 200 default is
        # plenty of headroom over the typical interactive
        # /convert flow (handfuls of jobs); audit-dispatched
        # cells are filtered out below so the upper bound is
        # almost never hit in practice.
        limit=min(max(int(limit), 1), 500),
        # Audit-dispatched cells are tagged with the admin's
        # user_sub but they belong to a batch sweep, not an
        # interactive /convert click. Filtering them out here
        # keeps the bottom-right toast a reflection of "things
        # I just clicked Convert on" — the Audit Runs admin tab
        # is the right surface for batch progress.
        exclude_audit_dispatched=True,
    )
    return JSONResponse({"jobs": rows})


@router.post("/plugins/{plugin_id}/jobs")
async def api_plugin_job(
    plugin_id: str,
    request: Request,
    ctx: RestContext = Depends(rest_context),
    scope: str = "shared",
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Enqueue an on-demand backend job for a plugin — generic; core names no
    plugin.

    Body: ``{"options": dict, "derived_key": str | None, "derived_prefix":
    str | None, "capability": str | None}``. ``options`` is passed opaquely to
    the plugin's ``job_entrypoint``; the returned summary lands as JSON at
    ``derived_key`` and the plugin writes its sidecar bundle under its reserved
    prefix (``derived_prefix``). Poll via ``GET /api/convert/{job_id}``.
    """
    body = await request.json()
    options = body.get("options") or {}
    if not isinstance(options, dict):
        raise HTTPException(status_code=400, detail="options must be a dict")

    scope_obj = parse_scope(scope, user)
    scope_obj = await resolve_project_scope(getattr(request.app.state, "db_pool", None), scope_obj)
    if not await scope_can_access(user, scope_obj, getattr(request.app.state, "db_pool", None)):
        raise HTTPException(status_code=403, detail="forbidden")

    # This route is generic ("core names no plugin"), so there is no single
    # field in ``options`` that reliably names the source file — one plugin
    # might call it ``sin_key``, another might not take a source at all.
    # Scan every string value instead of guessing a field name: any one of
    # them that names a key with an upload still in flight is the same
    # hazard /convert and /fea/manifest gate on, whatever the plugin calls
    # it in its own options shape.
    for opt_value in options.values():
        if not isinstance(opt_value, str) or not opt_value:
            continue
        pending = pending_uploads.get(scope_obj, opt_value)
        if pending is not None:
            raise HTTPException(status_code=409, detail=pending_upload_detail(opt_value, pending))

    # Read the advertised spec ONCE: it decides both whether this request
    # needs an admin and which pool it routes to.
    plugin_spec: dict | None = None
    for _spec in (await live_worker_specs(ctx.queue, "plugin_specs")).values():
        if _spec.get("slug") == plugin_id or _spec.get("id") == plugin_id:
            plugin_spec = _spec
            break

    # Scope access is not the same question as who may RUN this. A plugin
    # job can drive a licensed workstation for minutes; some are for admins
    # even among users who can read the scope it writes into.
    if await plugin_job_requires_admin(plugin_id, getattr(request.app.state, "db_pool", None), plugin_spec):
        if not getattr(user, "is_admin", False):
            raise HTTPException(
                status_code=403,
                detail=f"plugin job {plugin_id!r} is restricted to administrators",
            )

    # Everything from here is shared with the plugin-job scheduler -- see
    # enqueue_plugin_job. The checks above (scope access, the admin gate) are
    # this route's alone, which is why they are not in the helper.
    opts_hash = hashlib.sha256(json.dumps(options, sort_keys=True).encode("utf-8")).hexdigest()[:16]
    derived_key = body.get("derived_key")
    if not isinstance(derived_key, str) or not derived_key.strip():
        derived_key = f"_derived/plugin_jobs/{plugin_id}/{opts_hash}.json"
    job_id = await enqueue_plugin_job(
        ctx,
        plugin_id=plugin_id,
        options=options,
        scope_obj=scope_obj,
        capability=body.get("capability"),
        user=user,
        request=request,
        derived_prefix=body.get("derived_prefix"),
        derived_key=derived_key,
        plugin_spec=plugin_spec,
    )
    return JSONResponse({"job_id": job_id, "derived_key": derived_key})
