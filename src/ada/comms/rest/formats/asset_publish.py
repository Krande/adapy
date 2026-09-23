"""Publish staged blobs into the asset store (``asset_publish``).

Synthetic, like ``asset_build``: the staged bytes are already in the scope, and the job carries
the provider id, the staged keys and the caller's opaque options.

WHY A JOB AND NOT JUST A ROUTE. A derivation opens the staged file with the provider's own reader
-- an IFC file is parsed, walked and projected -- which is seconds to minutes of CPU on a file
that can be hundreds of megabytes. Doing that inside the request would hold a worker thread of
the API for the duration and give the caller a timeout instead of a progress bar.

THE WRITE IS CORE'S, NOT THE PROVIDER'S. ``derive()`` returns a plan; this handler stamps
authorship into every manifest (the owner gate -- a provider that set ``published_by`` is refused
by name), checks occupancy, and writes in the plan's order so manifests land last. See
``ada.assets.publish``.
"""

from __future__ import annotations

import asyncio
import json
import traceback as tb_module

import asyncpg

from ada.assets.keys import ASSET_PREFIX
from ada.assets.manifest import Actor
from ada.assets.publish import PublishError, PublishPlan, apply_publish_plan
from ada.assets.publishers import AssetPublisherError, asset_publisher
from ada.config import logger

from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job, JobQueue
from ..storage import Storage
from ..worker.audit import _audit_done
from ..worker.source_nodes import _SyncStorageFacade
from .registry import JobContext, SyntheticFormatHandler

ASSET_PUBLISH_KIND = "asset_publish"


async def _run_asset_publish(
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
    provider_id = opts.get("provider")
    staged = opts.get("staged") or {}
    if not provider_id or not staged:
        msg = f"conversion_options needs 'provider' and 'staged' for an {ASSET_PUBLISH_KIND} job"
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="publish", error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at)
        return

    try:
        publisher = asset_publisher(provider_id)
    except AssetPublisherError as exc:
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="publish", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at)
        return

    dry_run = bool(opts.get("dry_run"))
    replace_existing = bool(opts.get("replace"))
    collection = opts.get("collection")
    published_by = Actor(
        id=str(opts.get("published_by_id") or "unknown"),
        display=opts.get("published_by_display"),
        application=opts.get("published_by_application"),
    )
    published_via = str(opts.get("published_via") or "user")

    loop = asyncio.get_running_loop()
    sync_storage = _SyncStorageFacade(storage, scope, loop)

    def _derive() -> PublishPlan:
        return publisher.derive(
            scope,
            dict(staged),
            storage=sync_storage,
            collection=collection,
            options=dict(opts.get("options") or {}),
            dry_run=dry_run,
        )

    try:
        await queue.update(job_id, stage="derive", progress=0.10)
        plan = await loop.run_in_executor(None, _derive)
    except Exception as exc:
        logger.exception("worker: asset_publish derive failed for job %s", job_id)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="derive", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at, traceback=tb_module.format_exc())
        return

    # Occupancy is read HERE, not inside the provider: "is this revision already published" is a
    # question about the store, which core owns, and answering it per provider would give as many
    # answers as there are providers.
    try:
        touched = {k.rsplit("/", 1)[0] for k in (w.key for w in plan.writes)}
        occupied: set[str] = set()
        for prefix in sorted(touched):
            if not prefix.startswith(f"{ASSET_PREFIX}/"):
                continue
            entries = await storage.list_prefix(scope, f"{prefix}/")
            occupied.update(e.key for e in entries)
    except Exception as exc:
        logger.exception("worker: asset_publish occupancy check failed for job %s", job_id)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="publish", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at)
        return

    def _write(key: str, data: bytes) -> None:
        sync_storage.put_bytes(key, data)

    try:
        await queue.update(job_id, stage="publish", progress=0.60)
        outcome = await loop.run_in_executor(
            None,
            lambda: apply_publish_plan(
                plan,
                published_by=published_by,
                published_via=published_via,
                dry_run=dry_run,
                replace_existing=replace_existing,
                occupied=occupied,
                write=_write,
            ),
        )
    except PublishError as exc:
        # The refusals core makes on purpose: the owner gate, an occupied revision, a plan whose
        # order would leave a manifest describing blobs that are not there yet.
        logger.error("worker: asset_publish %s refused: %s", job_id, exc)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="publish", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at)
        return
    except Exception as exc:
        logger.exception("worker: asset_publish write failed for job %s", job_id)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="publish", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at, traceback=tb_module.format_exc())
        return

    try:
        await queue.update(job_id, stage="upload", progress=0.95)
        await storage.put_bytes(
            scope, job.derived_key, json.dumps(outcome.to_dict()).encode("utf-8"), content_encoding="gzip"
        )
    except Exception as exc:
        logger.exception("worker: asset_publish summary upload failed for job %s", job_id)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="upload", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at)
        return

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    logger.info(
        "worker: asset_publish %s -> %s@%s (%d subject(s)%s) job %s",
        provider_id,
        outcome.collection,
        outcome.revision,
        len(outcome.subjects),
        ", dry run" if outcome.dry_run else "",
        job_id,
    )
    await _audit_done(db_pool, job_id, "done", None, started_at)


class AssetPublishHandler(SyntheticFormatHandler):
    kind = ASSET_PUBLISH_KIND

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run_asset_publish(
            job=job,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
        )
