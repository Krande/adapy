"""Synthetic component build from a registered ConnectionSpec (``component_build``).
"""

from __future__ import annotations

import asyncio
import traceback as tb_module

import asyncpg

from ada.config import logger
from ada.core.file_system import new_temp_path

from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job, JobQueue
from ..storage import Storage
from ..worker.audit import _audit_done
from .registry import JobContext, SourceFormatHandler, SyntheticFormatHandler


async def _run_component_build(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    """Build a Connection GLB from a registered ConnectionSpec + inputs.

    Inputs are carried in ``job.conversion_options`` as
    ``{"spec_name": ..., "inputs": ..., "name": ..., "extra_handler_kwargs": {...}}``.
    The GLB lands at ``job.derived_key`` (typically
    ``_derived/component_builds/<job_id>.glb``); the frontend then
    fetches it via the standard blob GET. Runs in-process (pure Python
    via adapy + the registered handler) since handler imports happen
    at module load time in the worker process.
    """
    job_id = job.job_id
    opts = job.conversion_options or {}
    spec_name = opts.get("spec_name")
    inputs = opts.get("inputs") or {}
    component_name = opts.get("name")
    extra_kwargs = opts.get("extra_handler_kwargs") or {}

    if not spec_name:
        await queue.update(
            job_id,
            status=JOB_STATUS_ERROR,
            stage="build",
            error="conversion_options.spec_name is required for component_build",
        )
        await _audit_done(db_pool, job_id, "error", "missing spec_name", started_at)
        return

    from ada.api.connections import build_component

    loop = asyncio.get_running_loop()

    def _build_and_serialize() -> bytes:
        conn = build_component(
            spec_name=spec_name,
            inputs=inputs,
            name=component_name,
            **extra_kwargs,
        )
        glb_path = new_temp_path(suffix=".glb")
        try:
            conn.to_gltf(glb_path)
            return glb_path.read_bytes()
        finally:
            glb_path.unlink(missing_ok=True)

    try:
        await queue.update(job_id, stage="build", progress=0.40)
        glb_bytes = await loop.run_in_executor(None, _build_and_serialize)
    except Exception as exc:
        logger.exception("worker: component_build failed for %s", spec_name)
        trace = tb_module.format_exc()
        await queue.update(
            job_id,
            status=JOB_STATUS_ERROR,
            stage="build",
            error=str(exc),
        )
        await _audit_done(
            db_pool,
            job_id,
            "error",
            str(exc),
            started_at,
            traceback=trace,
        )
        return

    try:
        await queue.update(job_id, stage="upload", progress=0.90)
        # gzip-at-rest (see the conversion path) so the presigned GET serves it
        # Content-Encoding: gzip and the browser decompresses on the fly.
        await storage.put_bytes(scope, job.derived_key, glb_bytes, content_encoding="gzip")
    except Exception as exc:
        logger.exception("worker: component_build upload failed for %s", spec_name)
        trace = tb_module.format_exc()
        await queue.update(
            job_id,
            status=JOB_STATUS_ERROR,
            stage="upload",
            error=str(exc),
        )
        await _audit_done(
            db_pool,
            job_id,
            "error",
            str(exc),
            started_at,
            traceback=trace,
        )
        return

    await queue.update(
        job_id,
        status=JOB_STATUS_DONE,
        stage="ready",
        progress=1.0,
        error=None,
    )
    await _audit_done(db_pool, job_id, "done", None, started_at)


class ComponentBuildHandler(SyntheticFormatHandler if not False else SourceFormatHandler):
    kind = "component_build"

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run_component_build(
            job=job,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
        )
