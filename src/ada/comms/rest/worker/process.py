"""One job, end to end: the shared pre/post steps around a format handler — cancel/poison
guards, cached-blob short-circuit, source download, conversion settings, progress plumbing.
"""

from __future__ import annotations

import os
import pathlib
import tempfile
import time
from concurrent.futures import (  # noqa: F401 — kept for the legacy _process_one signature
    ThreadPoolExecutor,
)
from typing import Awaitable, Callable

import asyncpg

from ada.config import logger

from .. import db as db_module
from ..formats.registry import JobContext, resolve
from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, JOB_STATUS_RUNNING, JobQueue
from ..storage import Storage
from . import state
from .audit import _audit_done, _report_job_status_over_api
from .blobs import fetch_source
from .pools import MAX_DELIVERIES
from .settings import read_conversion_settings
from .state import _scope_of, _touch_liveness


async def _should_skip_cancelled(db_pool: "asyncpg.Pool | None", job_id: str) -> bool:
    """True when a job was already cancelled before this worker picked it up.

    Best-effort by design, and the two failure directions are not symmetric:
    answering False for a cancelled job costs one job run that the mid-run
    cancel checks then stop, while refusing to run on a failed query would let
    a database hiccup silently drain the queue. So anything unexpected means
    "run it".
    """
    if db_pool is None:
        return False
    try:
        return await db_module.audit_is_cancelled(db_pool, job_id)
    except Exception:
        logger.debug("worker: pre-run cancel check failed for %s", job_id, exc_info=True)
        return False


async def _process_one(
    job_id: str,
    queue: JobQueue,
    storage: Storage,
    pool: ThreadPoolExecutor | None,
    db_pool: asyncpg.Pool | None,
    delivery_count: int = 1,
) -> None:
    # ``pool`` is unused since the convert call moved into a forked
    # subprocess (see subprocess_convert.run_isolated_convert). The
    # parameter stays so the caller signature is unchanged for now;
    # remove once we're sure no tests reach in for the executor handle.
    del pool
    started_at = time.monotonic()
    job = await queue.get(job_id)
    if job is None:
        logger.warning("worker: job %s not found in KV; skipping", job_id)
        return

    scope = _scope_of(job)

    # Pre-download cancel skip: a cell whose (audit) run was cancelled is acked +
    # skipped here, BEFORE any source download or convert — so a cancelled run's
    # queued backlog costs ~nothing and can't wedge the worker on a doomed job.
    # (A *deleted* run's rows are gone, so audit_is_cancelled can't catch those —
    # the run cancel/delete endpoints purge those messages from the stream up front.)
    if db_pool is not None:
        try:
            if await db_module.audit_is_cancelled(db_pool, job_id):
                logger.info("worker: job %s cancelled; skipping before download", job_id)
                await queue.update(job_id, status="cancelled", stage="cancelled", progress=1.0, error=None)
                return
        except Exception:
            logger.exception("worker: pre-download cancel check failed for job %s", job_id)

    # Poison-pill guard: if NATS has redelivered this message past
    # the cap, the previous attempts crashed the worker before they
    # could ack. Stop trying — record the error, ack the message,
    # and let the queue drain so legitimate jobs aren't blocked.
    if delivery_count > MAX_DELIVERIES:
        msg = (
            f"worker exceeded {MAX_DELIVERIES} delivery attempts on this job "
            f"(prior runs likely crashed the worker process)."
        )
        logger.warning("worker: job %s gave up after %d attempts", job_id, delivery_count)
        await queue.update(
            job_id,
            status=JOB_STATUS_ERROR,
            stage="aborted",
            progress=0.0,
            error=msg,
        )
        await _audit_done(db_pool, job_id, "error", msg, started_at)
        return

    # Skip if a previous run already produced the derived blob. This is
    # the cheap safety net for redelivered messages.
    #
    # ``force_rebuild`` (set by the admin audit dispatcher when the
    # operator picks the cache-bypass option) makes us re-run even
    # if the blob exists — otherwise an audit measurement run would
    # see every cell short-circuit at ~5 ms each and the
    # ``duration_ms`` numbers would lie about actual conversion
    # cost. Regular convert jobs leave this False so the
    # redelivery safety-net still works.
    # A handler may opt out (``skip_cached_short_circuit``): equipment_bbox's real
    # output is the inferred bbox merged into the equipment doc (a DB side-effect),
    # NOT the derived preview.glb blob. The preview key isn't revision-stamped, so a
    # cached preview would short-circuit every re-infer and the bbox would never be
    # (re)applied — leaving it stuck at the archetype default.
    handler = resolve(job)
    if (
        not getattr(job, "force_rebuild", False)
        and not handler.skip_cached_short_circuit
        and await storage.exists(scope, job.derived_key)
    ):
        await queue.update(
            job_id,
            status=JOB_STATUS_DONE,
            stage="cached",
            progress=1.0,
            error=None,
        )
        await _audit_done(db_pool, job_id, "done", None, started_at)
        return

    await queue.update(job_id, status=JOB_STATUS_RUNNING, stage="loading", progress=0.05)
    # Mark the audit_log row matching this job as ``running`` (best-
    # effort). Without this the admin "current cell" toast can't
    # tell which queued row the worker is actually on, and the
    # display sticks to the same cell for the whole sweep.
    if db_pool is not None:
        try:
            await db_module.mark_audit_running(
                db_pool,
                job_id=job_id,
                worker_image_tag=state._WORKER_IMAGE_TAG,
            )
        except Exception:
            logger.exception("worker: audit running-mark failed for job %s", job_id)
    else:
        # No pool: report it over the API instead, so the Audit tab does not show
        # this job as queued for the whole time it is running.
        await _report_job_status_over_api(job_id, {"status": "running", "worker_image_tag": state._WORKER_IMAGE_TAG})

    ctx = JobContext(scope=scope, storage=storage, queue=queue, db_pool=db_pool, started_at=started_at)

    # Synthetic jobs (no source file): the model doc lives in postgres or the job
    # carries its inputs in ``conversion_options`` — run before any source download.
    if not handler.needs_source:
        await handler.run(job, ctx)
        return

    # Stream source to a worker-local tempfile rather than buffering
    # the whole payload in RAM. Big result decks (Sesam SIF can be
    # 950 MB+) blow up the worker pod otherwise; smaller sources still
    # benefit from skipping the bytes/path round-trip.
    src_suffix = pathlib.PurePosixPath(job.source_key).suffix or ""
    src_fd, src_name = tempfile.mkstemp(suffix=src_suffix)
    os.close(src_fd)
    src_path = pathlib.Path(src_name)
    try:
        try:
            ctx.fetch = await fetch_source(storage, scope, job, src_path)
        except FileNotFoundError as exc:
            logger.warning("worker: source %s missing for job %s", job.source_key, job_id)
            await queue.update(job_id, status=JOB_STATUS_ERROR, stage="loading", error=str(exc))
            await _audit_done(db_pool, job_id, "error", str(exc), started_at)
            return

        ctx.settings = await read_conversion_settings(db_pool, job)
        ctx.on_progress = _progress_callback(queue, job_id)
        ctx.src_path = src_path
        await handler.run(job, ctx)
    finally:
        try:
            src_path.unlink()
        except OSError:
            pass


def _progress_callback(queue: JobQueue, job_id: str) -> Callable[[str, float], Awaitable[None]]:
    # Forward progress from the converter to the KV-backed queue,
    # throttled so a chatty stage doesn't spam writes.
    last_kv_write = 0.0

    async def _on_progress(stage: str, frac: float) -> None:
        nonlocal last_kv_write
        _touch_liveness()  # a long conversion blocks the pull loop; progress keeps liveness fresh
        now = time.monotonic()
        if now - last_kv_write < 0.25 and frac < 1.0:
            return
        last_kv_write = now
        try:
            await queue.update(job_id, stage=stage, progress=frac)
        except Exception:
            logger.debug("queue.update from progress callback failed", exc_info=True)

    return _on_progress
