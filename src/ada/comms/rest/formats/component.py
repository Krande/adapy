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

    mode = opts.get("mode")
    if mode in ("report", "subject"):
        await _run_reported_build(
            job=job,
            scope=scope,
            storage=storage,
            queue=queue,
            db_pool=db_pool,
            started_at=started_at,
            spec_name=spec_name,
            inputs=inputs,
            mode=mode,
        )
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


#: Beside the build's derived key: what the studio's connection builder shows of it, or why it failed.
REPORT_SUFFIX = ".report.json"
ERROR_SUFFIX = ".error.json"


async def _run_reported_build(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
    spec_name: str,
    inputs: dict,
    mode: str,
) -> None:
    """The connection builder's build (``mode: "report"``) or simulation subject (``"subject"``),
    exactly as ``ada dev connections`` makes them (``ada.api.connections.library``): the GLB (or
    the weld assembly) at the job's derived key, ``<key>.report.json`` beside it with the members,
    welds, measured clash and the builder's log -- and on a failure ``<key>.error.json`` with the
    builder's traceback and whether it declined the members (NotApplicable), so the studio can tell
    a refusal from a crash."""
    import json as _json

    from ada.api.connections.library import (
        build_subject,
        build_with_report,
        format_user_traceback,
        is_declined,
    )

    job_id = job.job_id
    loop = asyncio.get_running_loop()
    suffix = ".glb" if mode == "report" else ".json"

    def _build() -> tuple[bytes, dict]:
        out = new_temp_path(suffix=suffix)
        try:
            if mode == "report":
                report = build_with_report(spec_name, inputs, out)
            else:
                report = build_subject(spec_name, inputs, out)
            return out.read_bytes(), report
        finally:
            out.unlink(missing_ok=True)

    await queue.update(job_id, stage="build", progress=0.40)
    try:
        data, report = await loop.run_in_executor(None, _build)
    except Exception as exc:  # noqa: BLE001 - every failure is reported to the studio
        logger.info("worker: connection build of %s failed: %s", spec_name, exc)
        failure = {
            "error": str(exc) or type(exc).__name__,
            "traceback": format_user_traceback(exc),
            "declined": is_declined(exc),
        }
        try:
            await storage.put_bytes(scope, job.derived_key + ERROR_SUFFIX, _json.dumps(failure).encode("utf-8"))
        except Exception:  # noqa: BLE001 - the job's error still says what went wrong
            logger.exception("worker: could not store the failure of %s", spec_name)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="build", error=failure["error"])
        await _audit_done(db_pool, job_id, "error", failure["error"], started_at, traceback=failure["traceback"])
        return

    try:
        await queue.update(job_id, stage="upload", progress=0.90)
        # gzip at rest, as the other builds: the browser decompresses on the fly.
        await storage.put_bytes(scope, job.derived_key, data, content_encoding="gzip")
        await storage.put_bytes(
            scope, job.derived_key + REPORT_SUFFIX, _json.dumps(report, default=str).encode("utf-8")
        )
    except Exception as exc:
        logger.exception("worker: connection build upload failed for %s", spec_name)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="upload", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at, traceback=tb_module.format_exc())
        return

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
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
