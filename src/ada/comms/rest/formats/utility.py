"""Worker utility against the loaded scene GLB (``utility``), e.g. diff.
"""

from __future__ import annotations

import asyncio
import pathlib
import traceback as tb_module

import asyncpg

from ada.config import logger

from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job, JobQueue
from ..storage import Storage
from ..worker.audit import _audit_done
from ..worker.source_nodes import _SyncStorageFacade
from .registry import JobContext, SourceFormatHandler, SyntheticFormatHandler


async def _run_utility_job(
    *,
    job: Job,
    src_path: pathlib.Path,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
    _on_progress,
) -> None:
    """Run a worker @utility against the loaded scene GLB.

    ``conversion_options`` carries ``{"utility_name": ..., "kwargs": {...}}``. The
    handler returns a viewer-ops dict stored as JSON at ``job.derived_key`` (a
    ``*.viewops.json`` key the API set at enqueue). The handler may also write
    auxiliary blobs (e.g. an overlay GLB) via the sync storage facade and
    reference them by key in the payload.
    """
    import json

    from ..utility import run_utility

    job_id = job.job_id
    opts = job.conversion_options or {}
    uname = opts.get("utility_name")
    ukwargs = opts.get("kwargs") or {}
    if not uname:
        await queue.update(
            job_id,
            status=JOB_STATUS_ERROR,
            stage="utility",
            error="conversion_options.utility_name is required for a utility job",
        )
        await _audit_done(db_pool, job_id, "error", "missing utility_name", started_at)
        return

    loop = asyncio.get_running_loop()
    sync_storage = _SyncStorageFacade(storage, scope, loop)

    # run_utility calls on_progress SYNCHRONOUSLY from the executor thread, but _on_progress
    # is an async coroutine (it writes the KV queue). Schedule it onto the loop so utility
    # stage/progress updates actually land in the job row — the same channel model loading /
    # conversions drive the global toast from. Fire-and-forget: we don't block the utility.
    def _sync_on_progress(stage: str, frac: float) -> None:
        try:
            asyncio.run_coroutine_threadsafe(_on_progress(stage, frac), loop)
        except Exception:  # noqa: BLE001 — a progress hiccup must never sink the utility
            pass

    def _invoke() -> dict:
        return run_utility(
            uname,
            src_path,
            storage=sync_storage,
            scope=scope,
            on_progress=_sync_on_progress,
            source_key=job.source_key,  # real model key — src_path is a random temp name
            kwargs=ukwargs,
        )

    try:
        await queue.update(job_id, stage="utility", progress=0.30)
        payload = await loop.run_in_executor(None, _invoke)
    except Exception as exc:
        logger.exception("worker: utility %s failed for job %s", uname, job_id)
        trace = tb_module.format_exc()
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="utility", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at, traceback=trace)
        return

    try:
        await queue.update(job_id, stage="upload", progress=0.90)
        await storage.put_bytes(scope, job.derived_key, json.dumps(payload).encode("utf-8"))
    except Exception as exc:
        logger.exception("worker: utility %s upload failed for job %s", uname, job_id)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="upload", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at)
        return

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    await _audit_done(db_pool, job_id, "done", None, started_at)


class UtilityHandler(SyntheticFormatHandler if not True else SourceFormatHandler):
    kind = "utility"

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run_utility_job(
            job=job,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
            src_path=ctx.src_path,
            _on_progress=ctx.on_progress,
        )
