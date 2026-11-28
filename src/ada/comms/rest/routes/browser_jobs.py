"""``/api/scopes/{scope}/browser-jobs/{job_id}/...`` -- a core job the browser runs (a claim).

See :mod:`..browser_jobs` for the contract. These routes take a claim (``start``), store what the
browser produced (``outputs/{name}``), end it (``complete``), or hand it to the worker pool it
would have gone to (``to-worker``) when the browser cannot run it and the execution policy lists
the worker next.
"""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from .. import auth as auth_module
from ..auth import User
from ..browser_jobs import (
    BROWSER_STAGE,
    FAMILY_FEATURE,
    RUN_ON_BROWSER,
    RUN_ON_WORKER,
    OutputError,
    claim_document,
    family_of,
    output_target,
    validate_output,
)
from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, JOB_STATUS_QUEUED, Job
from ..scope import Scope
from .deps import RestContext, optional_pool, rest_context, scope_from_path

router = APIRouter()

#: The largest output the browser may store in one request (a whole model's selection export).
MAX_OUTPUT_BYTES = 512 * 1024 * 1024
_RUNNING = "running"


def _job_scope(job: Job) -> Scope:
    if job.scope_kind in ("shared", "providers"):
        return Scope(kind=job.scope_kind)  # type: ignore[arg-type]
    return Scope(kind=job.scope_kind, id=job.scope_id)  # type: ignore[arg-type]


async def _claim(ctx: RestContext, scope_obj: Scope, job_id: str, *, states: tuple[str, ...]) -> Job:
    """The claim ``job_id`` in ``scope_obj``, in one of ``states``. 404 for anything else: a job of
    another scope, a job a worker runs, or no job."""
    if not ctx.queue.enabled:
        raise HTTPException(status_code=404, detail="this server keeps no browser jobs (it has no job queue)")
    job = await ctx.queue.get(job_id)
    if job is None or job.run_on != RUN_ON_BROWSER or _job_scope(job) != scope_obj:
        raise HTTPException(status_code=404, detail=f"no browser job {job_id!r} in this scope")
    if job.status not in states:
        raise HTTPException(
            status_code=409, detail=f"browser job {job_id!r} is {job.status}, not {' or '.join(states)}"
        )
    return job


def _claim_of(job: Job) -> dict:
    from dataclasses import asdict

    return claim_document(asdict(job))


@router.post("/scopes/{scope}/browser-jobs/{job_id}/start")
async def api_browser_job_start(
    job_id: str,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Take a claim: queued -> running, once. Returns the claim (what the browser runs). A second
    tab asking for the same claim gets 409 and waits on the job's status instead."""
    job = await _claim(ctx, scope_obj, job_id, states=(JOB_STATUS_QUEUED,))
    job = await ctx.queue.update(job_id, status=_RUNNING, stage="browser: running", progress=0.05)
    return JSONResponse(_claim_of(job))


@router.put("/scopes/{scope}/browser-jobs/{job_id}/outputs/{name:path}")
async def api_browser_job_output(
    job_id: str,
    name: str,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """Store one output of a running claim, where the job's route said it goes
    (``browser_jobs.output_target``), after checking it as the worker's own would be."""
    job = await _claim(ctx, scope_obj, job_id, states=(_RUNNING,))
    claim = _claim_of(job)
    announced = request.headers.get("content-length")
    if announced is not None and announced.isdigit() and int(announced) > MAX_OUTPUT_BYTES:
        raise HTTPException(status_code=413, detail=f"output {name!r} exceeds {MAX_OUTPUT_BYTES} bytes")
    data = await request.body()
    try:
        key, encoding, how = output_target(claim, name)
        validate_output(claim, name, data)
    except OutputError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    pool = optional_pool(request)
    if how == "blob":
        await ctx.storage.put_bytes(scope_obj, key, data, content_encoding=encoding)
    elif how == "run":
        from ..clash_runs import write_run_and_index

        await write_run_and_index(ctx.storage, scope_obj, key, data, pool=pool)
    elif how == "states":
        from ..assoc_links import store_source_states

        await store_source_states(ctx.storage, scope_obj, json.loads(data), pool=pool)
    elif how == "subjects":
        from ..assoc_links import store_subject_links

        if key:
            await ctx.storage.put_bytes(scope_obj, key, data, content_encoding=encoding)
        await store_subject_links(ctx.storage, scope_obj, json.loads(data).get("subjects") or (), pool=pool)
    elif how == "bbox":
        from .equipment_types import _catalogs

        type_id = str(claim["conversion_options"].get("type_id") or "")
        await _catalogs(request, ctx).apply_inferred_bbox(scope_obj, type_id, json.loads(data))
    return JSONResponse({"name": name, "key": key, "size": len(data)})


async def _finish_audit(request: Request, job: Job, status: str, error: str | None, traceback: str | None) -> None:
    pool = optional_pool(request)
    if pool is None:
        return
    from .. import db as db_module

    try:
        await db_module.update_audit_by_job(
            pool,
            job_id=job.job_id,
            status=status,
            error=error,
            duration_ms=int(max(0.0, time.time() - (job.created_at or time.time())) * 1000),
            traceback=traceback,
            worker_image_tag="browser",
        )
    except Exception:  # noqa: BLE001 - the job's own status already says how it ended
        import logging

        logging.getLogger(__name__).exception("browser job %s: audit update failed", job.job_id)


@router.post("/scopes/{scope}/browser-jobs/{job_id}/complete")
async def api_browser_job_complete(
    job_id: str,
    body: dict,
    request: Request,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
) -> JSONResponse:
    """End a running claim. Body ``{status: "done" | "error", error?, traceback?}``. ``done`` needs
    the job's main output (its derived key) stored; a job that cannot say so ends as an error."""
    job = await _claim(ctx, scope_obj, job_id, states=(_RUNNING,))
    status = str(body.get("status") or "")
    if status not in (JOB_STATUS_DONE, JOB_STATUS_ERROR):
        raise HTTPException(status_code=400, detail="'status' must be 'done' or 'error'")
    error = str(body.get("error") or "") or None
    traceback = str(body.get("traceback") or "") or None
    if status == JOB_STATUS_DONE and not await ctx.storage.exists(scope_obj, job.derived_key):
        status, error = JOB_STATUS_ERROR, f"the browser reported done without storing {job.derived_key!r}"
    if status == JOB_STATUS_ERROR and not error:
        error = "the browser job failed"
    job = await ctx.queue.update(
        job_id,
        status=status,
        stage="ready" if status == JOB_STATUS_DONE else "browser",
        progress=1.0 if status == JOB_STATUS_DONE else job.progress,
        error=error,
    )
    await _finish_audit(request, job, status, error, traceback)
    # `id`, not `job_id`: ending a claim queued no job, and a client that reads a `job_id` in an
    # answer as "a worker job was queued" (a WASM-only check) must not count it as one.
    return JSONResponse({"id": job_id, "status": status, "derived_key": job.derived_key, "error": error})


@router.post("/scopes/{scope}/browser-jobs/{job_id}/to-worker")
async def api_browser_job_to_worker(
    job_id: str,
    request: Request,
    body: dict | None = None,
    scope_obj: Scope = Depends(scope_from_path),
    ctx: RestContext = Depends(rest_context),
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Hand a claim the browser could not run to the worker pool it would have gone to: the same
    job record (id, keys, options, audit row) published to its pool. An explicit worker choice, so
    judged as one: the execution policy must list the worker for the job's family, and the user
    must have worker access to its pool. Body ``{reason?}`` (why the browser gave up, logged)."""
    from ..execution_policy import require_execution_policy
    from ..worker_access import require_worker_access

    job = await _claim(ctx, scope_obj, job_id, states=(JOB_STATUS_QUEUED, _RUNNING))
    family = family_of(job.target_format, job.conversion_options)
    pool = optional_pool(request)
    await require_execution_policy(
        pool,
        user,
        explicit=True,
        feature=FAMILY_FEATURE.get(family or "", job.target_format),
        source_key=job.source_key,
        conversion_options=job.conversion_options,
    )
    await require_worker_access(pool, user, job.target_capability)
    reason = str((body or {}).get("reason") or "")[:500]
    job = await ctx.queue.update(job_id, run_on=RUN_ON_WORKER, status=JOB_STATUS_QUEUED, stage="queued", progress=0.0)
    await ctx.queue.publish(job)
    if reason:
        import logging

        logging.getLogger(__name__).info("browser job %s handed to the worker: %s", job_id, reason)
    return JSONResponse({"job_id": job_id, "derived_key": job.derived_key, "status": job.status})


__all__ = ["BROWSER_STAGE", "router"]
