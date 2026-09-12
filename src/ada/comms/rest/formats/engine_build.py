"""Wheel build for a ``kind:wheel`` procedural engine (``procedural_engine_build``).
"""

from __future__ import annotations

import asyncio
import os
import traceback as tb_module

import asyncpg

from ada.config import logger

from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job, JobQueue
from ..storage import Storage
from ..worker.audit import _audit_done
from .registry import JobContext, SourceFormatHandler, SyntheticFormatHandler


def _engine_deploy_key_path(secret_name: str | None) -> str | None:
    """Filesystem path of the SSH deploy key for an external engine, or None for a
    public repo. The manifest names a secret; the deployment mounts that key file
    and points ``ENGINE_DEPLOY_KEY_<SECRET>`` at it (secret name uppercased,
    non-alnum -> ``_``). None when unset — the clone then runs without a key."""
    if not secret_name:
        return None
    env = "ENGINE_DEPLOY_KEY_" + "".join(c.upper() if c.isalnum() else "_" for c in secret_name)
    return os.environ.get(env)


async def _run_procedural_engine_build(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    """Build a ``kind:wheel`` procedural engine's wheel from its git repo and
    store it under the hidden ``_engines/`` prefix.

    ``conversion_options`` carries ``{"engine_id"}``; the manifest (repo_url/ref/
    deploy_key_secret) is read from postgres. The wheel is a pure-python
    (``py3-none-any``) build the browser micropip-installs. The built wheel's key
    is recorded in the engine doc (``wheel_key``, no revision bump), mirroring
    :func:`_run_equipment_bbox`."""
    job_id = job.job_id
    opts = job.conversion_options or {}
    engine_id = opts.get("engine_id")

    async def _fail(stage: str, msg: str, trace: str | None = None) -> None:
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage=stage, error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at, traceback=trace)

    if not engine_id:
        await _fail("build", "conversion_options.engine_id is required for procedural_engine_build")
        return
    if db_pool is None:
        await _fail("build", "engine build requires DATABASE_URL on the worker")
        return

    from .. import db as db_module

    row = await db_module.get_procedural_engine(db_pool, engine_id)
    if row is None:
        await _fail("build", f"procedural engine {engine_id} not found")
        return
    doc = row.get("doc") or {}
    if doc.get("kind") != "wheel":
        await _fail("build", f"engine {engine_id} is not kind=wheel (got {doc.get('kind')!r})")
        return
    repo_url = doc.get("repo_url")
    ref = doc.get("ref") or "main"
    if not repo_url:
        await _fail("build", "engine manifest is missing repo_url")
        return
    ssh_key_path = _engine_deploy_key_path(doc.get("deploy_key_secret"))

    from ..engine_build import build_engine_wheel
    from ..procedural import engine_wheel_key

    loop = asyncio.get_running_loop()
    try:
        await queue.update(job_id, stage="build", progress=0.30)
        filename, wheel_bytes = await loop.run_in_executor(
            None, lambda: build_engine_wheel(repo_url, ref, ssh_key_path=ssh_key_path)
        )
    except Exception as exc:
        logger.exception("worker: procedural_engine_build failed for %s", engine_id)
        await _fail("build", str(exc), tb_module.format_exc())
        return

    key = engine_wheel_key(engine_id, filename)
    try:
        await queue.update(job_id, stage="upload", progress=0.90)
        # A wheel is an already-compressed zip — store as-is (no gzip re-encode).
        await storage.put_bytes(scope, key, wheel_bytes)
        await db_module.set_procedural_engine_wheel(db_pool, engine_id, key)
    except Exception as exc:
        logger.exception("worker: procedural_engine_build upload failed for %s", engine_id)
        await _fail("upload", str(exc), tb_module.format_exc())
        return

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    await _audit_done(db_pool, job_id, "done", None, started_at)


class ProceduralEngineBuildHandler(SyntheticFormatHandler if not False else SourceFormatHandler):
    kind = "procedural_engine_build"

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run_procedural_engine_build(
            job=job,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
        )
