"""Conversion and utility jobs: ``POST /scopes/{scope}/convert`` (enqueue, or
answer from the cached ``_derived/`` product), ``GET /scopes/{scope}/convert/targets``,
``POST /scopes/{scope}/utility`` (a worker ``@utility`` against a scene GLB) and the
job-status poll ``GET /convert/{job_id}`` that every job kind shares.

Needs the app's storage, queue, job transport and worker-registry snapshot through
:class:`~.deps.RestContext`. Extracted from ``create_app``; see ``routes/__init__``
for the pattern. ``convert/targets`` is a fixed segment after ``convert`` with no
parameterised sibling under ``/scopes/{scope}/convert``, and ``/convert/{job_id}``
has no fixed sibling, so registration order inside this group does not matter.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import auth as auth_module
from .. import local_jobs, pending_uploads
from ..auth import User
from ..converter import (
    TARGET_FORMATS,
    ConverterRegistry,
    UnsupportedFormat,
    derived_key_for,
    supported_targets_for,
)
from ..job_transport import JobRequest
from ..scope import Scope
from ..scope import can_access as scope_can_access
from .deps import (
    RestContext,
    audit_local_job_outcome,
    is_accepted_source,
    pending_upload_detail,
    rest_context,
    scope_from_path,
    worker_advertised_utilities,
)

router = APIRouter()


@router.post("/scopes/{scope}/convert")
async def api_scope_convert(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    body = await request.json()
    source_key = (body.get("source_key") or "").strip()
    target_format = (body.get("target_format") or "glb").strip().lower()
    # Optional FEA result selection: present only for SIF picks. We
    # don't 400 if these arrive on a non-SIF source — the
    # derived_key helper just ignores them.
    raw_step = body.get("step")
    raw_field = body.get("field")
    step: int | None = None
    field: str | None = None
    if raw_step is not None and raw_field is not None:
        try:
            step = int(raw_step)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="step must be an integer") from exc
        field = str(raw_field).strip() or None
        if not field:
            raise HTTPException(status_code=400, detail="field must be non-empty")
    # Optional per-conversion overrides. Allowlist is the union of:
    #   1. Names declared at any ``@converter(options=[...])``
    #      site — registry-driven path that flows the value to
    #      the handler as a kwarg.
    #   2. Legacy env-var-driven names whose consuming code in
    #      ada/occ/geom/surfaces.py still reads ``os.environ``.
    #      These stay until the surfaces.py path learns to take
    #      these as function parameters; migrating them retires
    #      the legacy half-union below.
    # Unknown keys are dropped rather than 400'd so a future-server-
    # old-client mix degrades to "global setting wins".
    _LEGACY_ENV_OPTS = {
        "use_sat_pcurves",
        "skip_shapefix",
        "profile_conversions",
    }
    _ALLOWED_OPTS = ConverterRegistry.all_options() | _LEGACY_ENV_OPTS
    raw_opts = body.get("conversion_options") or {}
    conversion_options: dict | None = None
    if isinstance(raw_opts, dict) and raw_opts:
        # Preserve native types (bool stays bool, int stays int)
        # so the worker's kwarg path forwards them as-is to the
        # handler. The legacy env-var path in worker.py already
        # ``str()``s on its way to the env-var write, so storing
        # natives doesn't regress that rail.
        cleaned: dict[str, object] = {}
        for k, v in raw_opts.items():
            if k not in _ALLOWED_OPTS:
                continue
            if v is None:
                cleaned[k] = None
            else:
                cleaned[k] = v
        if cleaned:
            conversion_options = cleaned
    if not source_key:
        raise HTTPException(status_code=400, detail="source_key required")
    pending = pending_uploads.get(scope_obj, source_key)
    if pending is not None:
        raise HTTPException(status_code=409, detail=pending_upload_detail(source_key, pending))
    if not await is_accepted_source(ctx.queue, ctx.worker_registry, source_key):
        raise HTTPException(status_code=415, detail=f"unsupported source format: {source_key}")
    if target_format not in TARGET_FORMATS:
        raise HTTPException(
            status_code=415,
            detail=f"unknown target_format {target_format!r}; allowed: {sorted(TARGET_FORMATS)}",
        )
    viable = supported_targets_for(source_key)
    if target_format not in viable:
        raise HTTPException(
            status_code=415,
            detail=f"target {target_format!r} not viable for source {source_key!r}; allowed: {viable}",
        )
    if not await ctx.storage.exists(scope_obj, source_key):
        raise HTTPException(status_code=404, detail=f"source not found: {source_key}")
    ctx.jobs.require("conversion")

    # A re-conversion (gallery "Re-convert") always re-runs and writes to the SEPARATE
    # ``_reconvert/`` namespace, so it never overwrites the ``_derived/`` audit product in a
    # corpus scope. Regular converts keep the cached ``_derived/`` short-circuit below.
    reconvert = bool(body.get("reconvert"))
    try:
        if reconvert:
            from ..converter import reconvert_key_for

            derived_key = reconvert_key_for(source_key, target_format)
        else:
            derived_key = derived_key_for(source_key, target_format, step=step, field=field)
    except UnsupportedFormat as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    if not reconvert and await ctx.storage.exists(scope_obj, derived_key):
        await ctx.audit(
            request,
            user,
            scope_obj,
            "convert",
            key=source_key,
            target_format=target_format,
            status="done",
        )
        return JSONResponse(
            {
                "job_id": "",
                "source_key": source_key,
                "derived_key": derived_key,
                "target_format": target_format,
                "status": "done",
                "progress": 1.0,
                "stage": "cached",
                "scope_kind": scope_obj.kind,
                "scope_id": scope_obj.id,
                "cached": True,
            }
        )

    try:
        # Without a queue this runs here, in a job process of this environment (the
        # worker's pipeline: job_transport / local_runner); with one, a worker picks it up.
        job = await ctx.jobs.submit(
            JobRequest(
                source_key=source_key,
                target_format=target_format,
                scope=scope_obj,
                feature="conversion",
                step=step,
                field_name=field,
                conversion_options=conversion_options,
                derived_key=derived_key,
                force_rebuild=reconvert,
            )
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("enqueue failed")
        await ctx.audit(
            request,
            user,
            scope_obj,
            "convert",
            key=source_key,
            target_format=target_format,
            status="error",
            error=str(exc),
        )
        raise HTTPException(status_code=503, detail=f"enqueue failed: {exc}") from exc

    await ctx.audit(
        request,
        user,
        scope_obj,
        "convert",
        key=source_key,
        target_format=target_format,
        status="queued",
        job_id=job.job_id,
    )
    # Without a queue no worker moves that row on; the server does, when the job ends.
    audit_local_job_outcome(request, ctx.jobs, job.job_id)
    payload = dict(job.payload)
    payload.setdefault("source_key", source_key)
    payload.setdefault("target_format", target_format)
    payload["cached"] = False
    return JSONResponse(payload, status_code=202)


@router.get("/scopes/{scope}/convert/targets")
async def api_scope_convert_targets(
    source_key: str,
    scope_obj: Scope = Depends(scope_from_path),  # auth + access check
) -> JSONResponse:
    return JSONResponse({"source_key": source_key, "targets": supported_targets_for(source_key)})


@router.post("/scopes/{scope}/utility")
async def api_scope_utility(
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    user: User = Depends(auth_module.current_user),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Run a worker @utility against a loaded scene GLB.

    Body: ``{"source_key": ..., "utility_name": ..., "kwargs": {...}}``. The
    utility runs as a NATS job (target_format ``utility``) and writes a
    viewer-ops JSON blob at the returned ``derived_key``; the SPA polls the
    job, fetches that blob, and applies the ops to the live scene. Always
    recomputed (``force_rebuild``) since the result depends on the kwargs,
    which aren't encoded in the derived key.
    """
    from ..utility import viewops_key_for

    body = await request.json()
    source_key = (body.get("source_key") or "").strip()
    utility_name = (body.get("utility_name") or "").strip()
    raw_kwargs = body.get("kwargs") or {}
    if not source_key:
        raise HTTPException(status_code=400, detail="source_key required")
    if not utility_name:
        raise HTTPException(status_code=400, detail="utility_name required")
    if not isinstance(raw_kwargs, dict):
        raise HTTPException(status_code=400, detail="kwargs must be an object")
    pending = pending_uploads.get(scope_obj, source_key)
    if pending is not None:
        raise HTTPException(status_code=409, detail=pending_upload_detail(source_key, pending))
    # Gate against the live worker-advertised utility set so we don't enqueue
    # a job no worker can serve.
    advertised = {u.get("name") for u in await worker_advertised_utilities(ctx.queue, ctx.worker_registry)}
    if advertised and utility_name not in advertised:
        raise HTTPException(
            status_code=404,
            detail=f"unknown utility {utility_name!r}; available: {sorted(n for n in advertised if n)}",
        )
    if not await ctx.storage.exists(scope_obj, source_key):
        raise HTTPException(status_code=404, detail=f"source not found: {source_key}")
    ctx.jobs.require("utilities")

    derived_key = viewops_key_for(source_key, utility_name)
    try:
        job = await ctx.jobs.submit(
            JobRequest(
                source_key=source_key,
                target_format="utility",
                scope=scope_obj,
                feature="utilities",
                conversion_options={"utility_name": utility_name, "kwargs": raw_kwargs},
                derived_key=derived_key,
                force_rebuild=True,
            )
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("utility enqueue failed")
        await ctx.audit(
            request,
            user,
            scope_obj,
            "utility",
            key=source_key,
            target_format="utility",
            status="error",
            error=str(exc),
        )
        raise HTTPException(status_code=503, detail=f"enqueue failed: {exc}") from exc

    await ctx.audit(
        request,
        user,
        scope_obj,
        "utility",
        key=source_key,
        target_format="utility",
        status="queued",
        job_id=job.job_id,
    )
    audit_local_job_outcome(request, ctx.jobs, job.job_id)
    payload = dict(job.payload)
    payload.setdefault("source_key", source_key)
    payload.setdefault("target_format", "utility")
    payload["utility_name"] = utility_name
    return JSONResponse(payload, status_code=202)


@router.get("/convert/{job_id}")
async def api_convert_status(
    job_id: str,
    user: User = Depends(auth_module.current_user),
    request: Request = ...,
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    # Job status is identified by a globally unique job_id, so the
    # URL doesn't carry a scope. We still enforce that the caller
    # could access the job's recorded scope before returning it.
    #
    # In-process plugin jobs first: they exist whether or not a queue does,
    # and their ids are namespaced so they can never collide with a queued
    # job's. Checking them before the `queue.enabled` gate is what lets one
    # polling loop in the frontend serve both paths.
    #
    # THE PROVIDER STORE. A job in the ``providers`` scope is an admin's --
    # every provider job is -- EXCEPT a build: a member opening a dataset
    # granted to their scope has core build it as a store job on their
    # behalf (one shared cache for every granted scope), and must be able to
    # poll it. Builds only, never a provider job, and the id is unguessable;
    # what the poll returns is a status and a derived key, not the data.
    def _readable_store_build(kind: str, target_format: str | None, derived_key: str | None) -> bool:
        if kind != "providers":
            return False
        if target_format == "asset_build":
            return True
        # An in-process job records no target format; a build's summary is
        # the only one composed under `_derived/assets/` (a plugin job's is
        # under `_derived/plugin_jobs/`).
        return target_format is None and str(derived_key or "").startswith("_derived/assets/")

    def _job_scope_of(kind: str, ident: str | None) -> Scope:
        # `shared` and `providers` carry no id; a record may hold "" or None.
        if kind in ("shared", "providers"):
            return Scope(kind=kind)  # type: ignore[arg-type]
        return Scope(kind=kind, id=ident)  # type: ignore[arg-type]

    local = local_jobs.registry.get(job_id)
    if local is not None:
        if not _readable_store_build(local.scope_kind, getattr(local, "target_format", None), local.derived_key):
            local_scope = _job_scope_of(local.scope_kind, local.scope_id)
            if not await scope_can_access(user, local_scope, getattr(request.app.state, "db_pool", None)):
                raise HTTPException(status_code=403, detail="forbidden")
        return JSONResponse(local.as_json())
    # An in-process id this registry does not hold is one an earlier server
    # started: the registry dies with its process. A page that remembered it
    # (browser storage outlives the server, and is shared by every local store
    # served on the same origin) must hear "gone", not the queue gate's 503,
    # which reads as "try again" and kept it polling for its whole ceiling.
    if job_id.startswith("local-"):
        raise HTTPException(status_code=404, detail=f"job {job_id} is not known to this server")
    if not ctx.queue.enabled:
        raise HTTPException(status_code=503, detail="conversion disabled (no NATS configured)")
    job = await ctx.queue.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"job {job_id} not found")
    if not _readable_store_build(job.scope_kind, job.target_format or "", job.derived_key):
        job_scope = _job_scope_of(job.scope_kind, job.scope_id)
        if not await scope_can_access(user, job_scope, getattr(request.app.state, "db_pool", None)):
            raise HTTPException(status_code=403, detail="forbidden")
    return JSONResponse(asdict(job))
