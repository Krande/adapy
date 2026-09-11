"""Admin worker-fleet + job-control routes: ``GET /api/admin/workers``,
``POST /api/admin/workers/prune``, ``GET
/api/admin/worker-packages/{image_tag:path}`` and ``POST
/api/admin/jobs/{job_id}/cancel``.

Needs the job queue (through :class:`~.deps.RestContext`) for the worker
registry, and the DB pool (``require_pool``) for the package-manifest
lookup. The job-cancel route reads ``request.app.state`` directly for both
the pool and the queue, same as it did inside the closure — it has no other
dependency on ``create_app``'s services.

Extracted from ``create_app``; see ``routes/__init__`` for the pattern.
"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from ada.config import logger

from .. import auth as auth_module
from .. import db as db_module
from .. import local_jobs
from ..auth import User
from .deps import RestContext, require_pool, rest_context

router = APIRouter()


@router.get("/workers")
async def admin_list_workers(ctx: RestContext = Depends(rest_context)) -> JSONResponse:
    """Snapshot of every worker pod that recently checked in.

    Each running worker re-PUTs its registry entry every 15 s; the
    admin panel marks rows older than 60 s as offline (kept in the
    list briefly so a flapping pod is visible while it restarts).
    The list itself is just the KV scan — no DB hit, safe to poll
    at the panel's refresh cadence.
    """
    queue = ctx.queue
    if not queue.enabled:
        raise HTTPException(
            status_code=503,
            detail="worker registry requires a NATS-backed queue",
        )
    try:
        workers = await queue.list_workers()
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"could not read worker registry: {exc}",
        ) from exc
    now = time.time()
    # Annotate each row with a derived ``online`` boolean so the
    # frontend doesn't have to recompute the staleness threshold.
    # Same window the routing path uses (queue._capability_for_ext) so
    # "shown online in the UI" and "eligible for auto-routing" agree.
    stale_after_s = queue.WORKER_STALE_AFTER_S
    for w in workers:
        hb = w.get("last_heartbeat")
        try:
            w["online"] = isinstance(hb, (int, float)) and (now - hb) <= stale_after_s
        except TypeError:
            w["online"] = False
    # Newest registration first; offline rows sink to the bottom so
    # the live fleet sits at the top of the table.
    workers.sort(
        key=lambda w: (not w.get("online"), -float(w.get("last_heartbeat") or 0)),
    )
    return JSONResponse({"workers": workers, "now": now, "stale_after_s": stale_after_s})


@router.post("/workers/prune")
async def admin_prune_workers(ctx: RestContext = Depends(rest_context)) -> JSONResponse:
    """Manually drop every currently-OFFLINE worker registry entry (heartbeat older than the
    staleness window). A live pod re-registers within a heartbeat tick, so this only clears dead
    registrations left by crashed / scaled-down pods — which otherwise linger and pollute the
    capability matrix. The hourly background task also prunes, but only at the conservative 2-day
    horizon; this button is the immediate manual cleanup."""
    queue = ctx.queue
    if not queue.enabled:
        raise HTTPException(
            status_code=503,
            detail="worker registry requires a NATS-backed queue",
        )
    try:
        pruned = await queue.prune_stale_workers(max_age_s=queue.WORKER_STALE_AFTER_S)
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail=f"could not prune worker registry: {exc}",
        ) from exc
    return JSONResponse({"pruned": pruned})


@router.get("/worker-packages/{image_tag:path}")
async def admin_worker_packages(image_tag: str, request: Request) -> JSONResponse:
    """The captured package manifest ("pixi list") for a worker image tag —
    linked from a convert audit row via its worker_image_tag."""
    pool = require_pool(request)
    manifest = await db_module.get_worker_packages(pool, image_tag)
    if manifest is None:
        raise HTTPException(status_code=404, detail=f"no package manifest for worker {image_tag!r}")
    return JSONResponse(manifest)


@router.post("/jobs/{job_id}/cancel")
async def admin_cancel_job(
    job_id: str,
    request: Request,
    user: User = Depends(auth_module.current_user),
) -> JSONResponse:
    """Cancel and clear any job, whoever started it. Admin only.

    THE JOB THIS EXISTS FOR is the one nothing will ever finish: queued
    against a capability no live worker serves, or left behind by a pool
    that was renamed or retired. Nothing pulls it, so it never reaches a
    terminal status, so ``purge_completed_jobs`` -- which sweeps terminal
    entries only -- never touches it. The entry stays in the KV bucket
    forever, is replayed by every registry scan, and reads in the admin
    panel as work still pending.

    The user-facing ``my-jobs/{job_id}/cancel`` could not clear these:
    its SQL filters on ``audit_log.user_sub``, so only the person who
    started a job can stop it, and an operator cleaning up after a retired
    pool is by definition not that person.

    TWO INDEPENDENT EFFECTS, BOTH REPORTED, because a stuck job can be
    stuck in either half alone:

    * ``cancelled`` -- the audit row moved from queued/running to
      cancelled. False if the row is missing or already terminal.
    * ``purged`` -- the KV entry was dropped. False if there was none.

    404 only when NEITHER did anything, i.e. there was no such job in
    either place. A row that is already terminal with a leftover KV entry
    is a real thing to clean up, and reporting that as "not found" would
    send an operator looking for a job that is right in front of them.

    Note the message may still be in the JetStream stream: this marks
    state, it does not reach into the stream. A worker that later pulls it
    checks the audit row before starting and drops it (see
    ``_should_skip_cancelled`` in worker.py), so a cancelled job stays
    cancelled -- but the message itself ages out on the stream's own
    limits rather than disappearing here.
    """
    local = local_jobs.registry.get(job_id)
    local_cancelled = local_jobs.registry.cancel(job_id) if local is not None else False

    pool = getattr(request.app.state, "db_pool", None)
    cancelled = False
    if pool is not None:
        cancelled = await db_module.admin_cancel_audit_by_job(
            pool,
            job_id=job_id,
            reason=f"cancelled by administrator {user.sub}",
        )

    purged = False
    queue_obj = getattr(request.app.state, "queue", None)
    if queue_obj is not None:
        try:
            purged = await queue_obj.purge_job(job_id)
        except Exception:
            logger.exception("admin: purging KV entry for job %s failed", job_id)

    if not (cancelled or purged or local_cancelled):
        raise HTTPException(status_code=404, detail="no such job in the audit log or the queue")

    logger.info(
        "admin: %s cancelled job %s (audit=%s kv=%s local=%s)",
        user.sub,
        job_id,
        cancelled,
        purged,
        local_cancelled,
    )
    return JSONResponse({"job_id": job_id, "cancelled": cancelled or local_cancelled, "purged": purged})
