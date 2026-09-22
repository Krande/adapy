"""Build one asset node on demand (``asset_build``) -- the worker half of Decision 1's ``build`` kind.

Synthetic: there is no source file to download. The job carries the identity core composed the
derived key from, plus the provider's OPAQUE options, and this handler:

1. resolves the builder registered for the job's CAPABILITY (never for a provider id),
2. runs it in an executor with the scope-bound sync storage facade and the derived prefix core
   composed, so the builder writes under a prefix it could not have chosen,
3. validates the returned summary's provenance against the request -- a build that answers a
   different request is refused HERE, at the writer, rather than at every reader,
4. stores the summary at ``job.derived_key``.

Why validation happens twice (here and in the browser, ``@/assets/delivery.ts``): this side
catches a builder bug at the moment it happens and keeps the bad summary out of the store; the
browser catches a stale or hand-placed blob under a key it did not write. Neither makes the other
redundant, and the message names the disagreeing field either way.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time
import traceback as tb_module

import asyncpg

from ada.assets.build import (
    BuildError,
    BuildSummary,
    parse_build_summary,
    validate_build_summary,
)
from ada.assets.builders import AssetBuilderError, BuildRequest, asset_builder
from ada.config import logger

from .. import db as db_module
from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job, JobQueue
from ..storage import Storage
from ..worker.audit import _audit_done
from ..worker.source_nodes import _SyncStorageFacade
from .registry import JobContext, SyntheticFormatHandler

ASSET_BUILD_KIND = "asset_build"


async def _run_asset_build(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    job_id = job.job_id
    opts = job.conversion_options or {}
    capability = opts.get("capability")
    request_fields = ("provider", "collection", "subject", "revision", "fingerprint", "hierarchy_source")
    missing = [f for f in (*request_fields, "capability") if not opts.get(f)]
    if missing:
        msg = f"conversion_options missing {', '.join(missing)} for an {ASSET_BUILD_KIND} job"
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="build", error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at)
        return

    request = BuildRequest(
        provider=opts["provider"],
        collection=opts["collection"],
        subject=opts["subject"],
        revision=opts["revision"],
        node=opts.get("node"),
        fingerprint=opts["fingerprint"],
        hierarchy_source=opts["hierarchy_source"],
    )
    derived_prefix = opts.get("derived_prefix") or job.derived_key.rsplit("/", 1)[0]

    try:
        builder = asset_builder(capability)
    except AssetBuilderError as exc:
        # Routing put this job on a pool that cannot serve it. That is a deployment fact, not a
        # provider bug, and the message says which capability went unserved.
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="build", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at)
        return

    loop = asyncio.get_running_loop()
    sync_storage = _SyncStorageFacade(storage, scope, loop)

    async def _aprog(stage: str, frac: float) -> None:
        await queue.update(job_id, stage=stage, progress=max(0.1, min(0.95, float(frac))))

    def _sync_on_progress(stage: str, frac: float) -> None:
        try:
            asyncio.run_coroutine_threadsafe(_aprog(stage, frac), loop)
        except Exception:  # noqa: BLE001 - progress must never sink a build
            pass

    # Cooperative cancellation, as the plugin path does it: the builder runs in a thread, so it
    # cannot be reaped by killing a child; a builder that ignores the event simply runs on.
    cancel_event = threading.Event()

    async def _cancel_poller() -> None:
        while True:
            await asyncio.sleep(2.0)
            try:
                if await db_module.audit_is_cancelled(db_pool, job_id):
                    cancel_event.set()
                    return
            except Exception:
                logger.debug("worker: asset_build cancel poll failed for %s", job_id, exc_info=True)

    poller = asyncio.create_task(_cancel_poller()) if db_pool is not None else None

    def _invoke():
        return builder.build(
            opts.get("options") or {},
            request=request,
            storage=sync_storage,
            scope=scope,
            derived_prefix=derived_prefix,
            on_progress=_sync_on_progress,
            cancel_event=cancel_event,
        )

    try:
        await queue.update(job_id, stage="build", progress=0.10)
        result = await loop.run_in_executor(None, _invoke)
    except Exception as exc:
        if cancel_event.is_set():
            logger.info("worker: asset_build %s cancelled by user mid-run", job_id)
            try:
                await queue.update(job_id, status="cancelled", stage="cancelled", progress=1.0, error=None)
            except Exception:
                pass
            await _audit_done(db_pool, job_id, "cancelled", "cancelled by user", started_at)
            return
        logger.exception("worker: asset_build failed for job %s", job_id)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="build", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at, traceback=tb_module.format_exc())
        return
    finally:
        if poller is not None:
            poller.cancel()

    try:
        summary = result if isinstance(result, BuildSummary) else parse_build_summary(result)
        validate_build_summary(
            summary,
            provider=request.provider,
            collection=request.collection,
            subject=request.subject,
            revision=request.revision,
            node=request.node,
            fingerprint=request.fingerprint,
            derived_prefix=derived_prefix,
        )
    except BuildError as exc:
        logger.error("worker: asset_build %s produced a summary core refused: %s", job_id, exc)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="validate", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at)
        return

    try:
        await queue.update(job_id, stage="upload", progress=0.95)
        await storage.put_bytes(
            scope, job.derived_key, json.dumps(summary.to_dict()).encode("utf-8"), content_encoding="gzip"
        )
    except Exception as exc:
        logger.exception("worker: asset_build summary upload failed for job %s", job_id)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="upload", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at)
        return

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    logger.info(
        "worker: asset_build %s/%s@%s node=%s finished job %s in %.1fs",
        request.collection,
        request.subject,
        request.revision,
        request.node or "all",
        job_id,
        time.monotonic() - started_at,
    )
    await _audit_done(db_pool, job_id, "done", None, started_at)


class AssetBuildHandler(SyntheticFormatHandler):
    kind = ASSET_BUILD_KIND

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run_asset_build(
            job=job,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
        )
