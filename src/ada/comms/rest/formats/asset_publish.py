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

THE ATTRIBUTE INDEX IS CORE'S TOO. A staged ``attribute-index.ndjson.gz``
(``ada.assets/attribute-index@1``) never reaches the provider's ``derive()``: core streams it into
Postgres after the plan is written (``db/attribute_index.py``), which is what makes
``POST /assets/search`` provider-neutral. A staging holding only that file publishes without any
provider publisher at all.
"""

from __future__ import annotations

import asyncio
import json
import time
import traceback as tb_module

import asyncpg

from ada.assets.keys import ASSET_PREFIX, STAGING_SEGMENT
from ada.assets.manifest import Actor
from ada.assets.publish import (
    PublishError,
    PublishPlan,
    apply_publish_plan,
    stored_encoding,
)
from ada.assets.publishers import AssetPublisherError, asset_publisher
from ada.config import logger

from ..db.attribute_index import (
    ATTRIBUTE_INDEX_ROLE,
    ingest_attribute_index,
    read_attribute_index_header,
)
from ..db.node_bounds import (
    NODE_BOUNDS_ROLE,
    ingest_node_bounds,
    read_node_bounds_header,
)
from ..db.node_relations import (
    NODE_RELATIONS_ROLE,
    ingest_node_relations,
    read_node_relations_header,
)
from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job, JobQueue
from ..storage import Storage
from ..worker.audit import _audit_done
from ..worker.source_nodes import _SyncStorageFacade
from .registry import JobContext, SyntheticFormatHandler

ASSET_PUBLISH_KIND = "asset_publish"

#: Staged roles core ingests itself; a provider's ``derive()`` never sees them.
_CORE_ROLES = frozenset({ATTRIBUTE_INDEX_ROLE, NODE_BOUNDS_ROLE, NODE_RELATIONS_ROLE})


def consumed_staging(staged: dict, *, dry_run: bool) -> list[str]:
    """The staged keys a FINISHED publish has consumed and may now clear.

    ``GET /assets/staging`` is the store's memory of what was staged and NOT YET PUBLISHED, and a
    staging left behind after its publish reads, for ever, as work nobody finished -- the listing
    stops meaning anything once every publish leaves an entry in it. So a real publish clears what
    it was handed. A dry run clears nothing: it is the preview a publish of the same staging
    follows. Only keys under the staging prefix, whatever a caller passed.
    """
    if dry_run:
        return []
    prefix = f"{ASSET_PREFIX}/{STAGING_SEGMENT}/"
    return sorted({str(k) for k in staged.values() if str(k).startswith(prefix)})


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

    # The attribute index is CORE's role, not the provider's: it is split off before the provider
    # sees the staging and ingested by core after the plan is written. A staging that holds
    # nothing else is an attributes-only publish -- no provider publisher is asked for at all.
    attr_key = staged.get(ATTRIBUTE_INDEX_ROLE)
    # Node bounds likewise (``db/node_bounds.py``): core's role, ingested after the plan.
    bounds_key = staged.get(NODE_BOUNDS_ROLE)
    # Node relations likewise (``db/node_relations.py``).
    relations_key = staged.get(NODE_RELATIONS_ROLE)
    plan_staged = {role: key for role, key in staged.items() if role not in _CORE_ROLES}

    dry_run = bool(opts.get("dry_run"))
    replace_existing = bool(opts.get("replace"))
    collection = opts.get("collection")
    published_by = Actor(
        id=str(opts.get("published_by_id") or "unknown"),
        display=opts.get("published_by_display"),
        application=opts.get("published_by_application"),
    )
    published_via = str(opts.get("published_via") or "user")

    # What each phase took, in the summary beside the outcome: a provider sweep records it per
    # collection (routes/asset_sweeps.py), and "the publish was slow" is otherwise unanswerable.
    timings: dict[str, int] = {}
    warnings: list[str] = []

    if plan_staged:
        try:
            publisher = asset_publisher(provider_id)
        except AssetPublisherError as exc:
            await queue.update(job_id, status=JOB_STATUS_ERROR, stage="publish", error=str(exc))
            await _audit_done(db_pool, job_id, "error", str(exc), started_at)
            return

        loop = asyncio.get_running_loop()
        sync_storage = _SyncStorageFacade(storage, scope, loop)

        def _derive() -> PublishPlan:
            return publisher.derive(
                scope,
                dict(plan_staged),
                storage=sync_storage,
                collection=collection,
                options=dict(opts.get("options") or {}),
                dry_run=dry_run,
            )

        t0 = time.perf_counter()
        try:
            await queue.update(job_id, stage="derive", progress=0.10)
            plan = await loop.run_in_executor(None, _derive)
            timings["derive_ms"] = round((time.perf_counter() - t0) * 1000)
        except Exception as exc:
            logger.exception("worker: asset_publish derive failed for job %s", job_id)
            await queue.update(job_id, status=JOB_STATUS_ERROR, stage="derive", error=str(exc))
            await _audit_done(db_pool, job_id, "error", str(exc), started_at, traceback=tb_module.format_exc())
            return

        # Occupancy is read HERE, not inside the provider: "is this revision already published"
        # is a question about the store, which core owns, and answering it per provider would
        # give as many answers as there are providers.
        t0 = time.perf_counter()
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
            # A hierarchy spine arrives gzip-compressed from apply_publish_plan (gzip at rest):
            # store it as-is and label it, so the blob route and presigned reads carry its
            # Content-Encoding.
            encoding = stored_encoding(data)
            sync_storage.put_bytes(key, data, content_encoding=encoding, pre_compressed=encoding is not None)

        timings["occupancy_ms"] = round((time.perf_counter() - t0) * 1000)
        t0 = time.perf_counter()
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
                    provider=str(provider_id),
                ),
            )
        except PublishError as exc:
            # The refusals core makes on purpose: the owner gate, an occupied revision, a plan
            # whose order would leave a manifest describing blobs that are not there yet.
            logger.error("worker: asset_publish %s refused: %s", job_id, exc)
            await queue.update(job_id, status=JOB_STATUS_ERROR, stage="publish", error=str(exc))
            await _audit_done(db_pool, job_id, "error", str(exc), started_at)
            return
        except Exception as exc:
            logger.exception("worker: asset_publish write failed for job %s", job_id)
            await queue.update(job_id, status=JOB_STATUS_ERROR, stage="publish", error=str(exc))
            await _audit_done(db_pool, job_id, "error", str(exc), started_at, traceback=tb_module.format_exc())
            return

        timings["write_ms"] = round((time.perf_counter() - t0) * 1000)
        timings["writes"] = len(plan.writes)
        summary = outcome.to_dict()
    else:
        summary = None

    # The attribute index, after the provider's plan is written. A failure here does not undo a
    # publish that already landed -- it is reported beside it, and the staged index is left
    # staged (the honest state: it was not consumed). For an attributes-only publish the index IS
    # the publish, and its failure is the job's.
    attributes: dict | None = None
    attr_consumed = False
    if attr_key:
        await queue.update(job_id, stage="ingest", progress=0.80)
        try:
            if dry_run or db_pool is None:
                header = await read_attribute_index_header((await storage.open_stream(scope, attr_key)).stream)
                attributes = {
                    "provider": provider_id,
                    "collection": header["collection"],
                    "revision": header.get("revision"),
                    "source_etag": header.get("source_etag"),
                    "ingested": False,
                }
                if db_pool is None and not dry_run:
                    warnings.append(
                        "this worker has no database pool (DATABASE_URL unset): the attribute index was NOT "
                        "ingested and stays staged -- publish it again from a worker with a database"
                    )
            else:
                ingested = await ingest_attribute_index(
                    db_pool,
                    scope=scope.prefix(),
                    provider=str(provider_id),
                    chunks=(await storage.open_stream(scope, attr_key)).stream,
                )
                phase = ingested.pop("timings")
                timings["ingest_ms"] = phase.get("ingest_ms", 0)
                timings["ingest_rows"] = ingested["rows"]
                timings["index_ms"] = phase.get("index_ms", 0)
                timings["swap_ms"] = phase.get("swap_ms", 0)
                warnings.extend(ingested.pop("warnings"))
                attributes = {**ingested, "ingested": True}
                attr_consumed = True
        except Exception as exc:
            if summary is None:
                logger.exception("worker: asset_publish attribute ingest failed for job %s", job_id)
                await queue.update(job_id, status=JOB_STATUS_ERROR, stage="ingest", error=str(exc))
                await _audit_done(db_pool, job_id, "error", str(exc), started_at, traceback=tb_module.format_exc())
                return
            logger.exception("worker: asset_publish %s published, but its attribute index failed", job_id)
            attributes = {"ingested": False, "error": str(exc)}
            warnings.append(f"the attribute index was not ingested: {exc}")

    # Node bounds, the same way: after the plan, a failure reported beside a publish that landed,
    # and the job's own failure when the bounds ARE the publish.
    bounds: dict | None = None
    bounds_consumed = False
    if bounds_key:
        await queue.update(job_id, stage="ingest", progress=0.88)
        try:
            if dry_run or db_pool is None:
                header = await read_node_bounds_header((await storage.open_stream(scope, bounds_key)).stream)
                bounds = {
                    "provider": provider_id,
                    "collection": header["collection"],
                    "revision": header.get("revision"),
                    "frame": header.get("frame"),
                    "ingested": False,
                }
                if db_pool is None and not dry_run:
                    warnings.append(
                        "this worker has no database pool (DATABASE_URL unset): the node bounds were NOT "
                        "ingested and stay staged -- publish them again from a worker with a database"
                    )
            else:
                ingested_bounds = await ingest_node_bounds(
                    db_pool,
                    scope=scope.prefix(),
                    provider=str(provider_id),
                    chunks=(await storage.open_stream(scope, bounds_key)).stream,
                )
                phase = ingested_bounds.pop("timings")
                timings["bounds_ms"] = phase.get("ingest_ms", 0) + phase.get("swap_ms", 0)
                timings["bounds_rows"] = ingested_bounds["rows"]
                warnings.extend(ingested_bounds.pop("warnings"))
                bounds = {**ingested_bounds, "ingested": True}
                bounds_consumed = True
        except Exception as exc:
            if summary is None and attributes is None:
                logger.exception("worker: asset_publish node bounds ingest failed for job %s", job_id)
                await queue.update(job_id, status=JOB_STATUS_ERROR, stage="ingest", error=str(exc))
                await _audit_done(db_pool, job_id, "error", str(exc), started_at, traceback=tb_module.format_exc())
                return
            logger.exception("worker: asset_publish %s published, but its node bounds failed", job_id)
            bounds = {"ingested": False, "error": str(exc)}
            warnings.append(f"the node bounds were not ingested: {exc}")

    # Node relations, the same way (``db/node_relations.py``).
    relations: dict | None = None
    relations_consumed = False
    if relations_key:
        await queue.update(job_id, stage="ingest", progress=0.9)
        try:
            if dry_run or db_pool is None:
                header = await read_node_relations_header((await storage.open_stream(scope, relations_key)).stream)
                relations = {
                    "provider": provider_id,
                    "collection": header["collection"],
                    "revision": header.get("revision"),
                    "ingested": False,
                }
                if db_pool is None and not dry_run:
                    warnings.append(
                        "this worker has no database pool (DATABASE_URL unset): the node relations were NOT "
                        "ingested and stay staged -- publish them again from a worker with a database"
                    )
            else:
                ingested_relations = await ingest_node_relations(
                    db_pool,
                    scope=scope.prefix(),
                    provider=str(provider_id),
                    chunks=(await storage.open_stream(scope, relations_key)).stream,
                )
                phase = ingested_relations.pop("timings")
                timings["relations_ms"] = phase.get("ingest_ms", 0) + phase.get("swap_ms", 0)
                timings["relations_rows"] = ingested_relations["rows"]
                warnings.extend(ingested_relations.pop("warnings"))
                relations = {**ingested_relations, "ingested": True}
                relations_consumed = True
        except Exception as exc:
            if summary is None and attributes is None and bounds is None:
                logger.exception("worker: asset_publish node relations ingest failed for job %s", job_id)
                await queue.update(job_id, status=JOB_STATUS_ERROR, stage="ingest", error=str(exc))
                await _audit_done(db_pool, job_id, "error", str(exc), started_at, traceback=tb_module.format_exc())
                return
            logger.exception("worker: asset_publish %s published, but its node relations failed", job_id)
            relations = {"ingested": False, "error": str(exc)}
            warnings.append(f"the node relations were not ingested: {exc}")

    if summary is None:
        # Attributes-, bounds- or relations-only: the outcome a caller (and a sweep) reads names the
        # collection and revision the header gave.
        head = attributes if attributes is not None else bounds if bounds is not None else relations
        assert head is not None
        counts = {}
        if attributes is not None:
            counts["attribute_rows"] = attributes.get("rows", 0)
        if bounds is not None:
            counts["bounds_rows"] = bounds.get("rows", 0)
        if relations is not None:
            counts["relation_rows"] = relations.get("rows", 0)
        summary = {
            "collection": head.get("collection"),
            "revision": head.get("revision"),
            "subjects": [],
            "written": [],
            "dry_run": dry_run,
            "replaced": [],
            "counts": counts,
        }
    if attributes is not None:
        summary["attributes"] = attributes
    if bounds is not None:
        summary["bounds"] = bounds
    if relations is not None:
        summary["relations"] = relations
    if warnings:
        summary["warnings"] = warnings

    try:
        await queue.update(job_id, stage="upload", progress=0.95)
        summary = {**summary, "timings": timings}
        await storage.put_bytes(scope, job.derived_key, json.dumps(summary).encode("utf-8"), content_encoding="gzip")
    except Exception as exc:
        logger.exception("worker: asset_publish summary upload failed for job %s", job_id)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="upload", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at)
        return

    # After the summary, so a publish is never reported as failed over its own cleanup; a key that
    # cannot be removed stays listed as staged, which is the honest state for it.
    cleared = dict(plan_staged)
    if attr_key and attr_consumed:
        cleared[ATTRIBUTE_INDEX_ROLE] = attr_key
    if bounds_key and bounds_consumed:
        cleared[NODE_BOUNDS_ROLE] = bounds_key
    if relations_key and relations_consumed:
        cleared[NODE_RELATIONS_ROLE] = relations_key
    for key in consumed_staging(cleared, dry_run=dry_run):
        try:
            await storage.delete(scope, key)
        except Exception:
            logger.warning("worker: asset_publish %s could not clear staged %s", job_id, key, exc_info=True)

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    logger.info(
        "worker: asset_publish %s -> %s@%s (%d subject(s)%s%s) job %s",
        provider_id,
        summary.get("collection"),
        summary.get("revision"),
        len(summary.get("subjects") or []),
        (
            f", attribute index shared from {attributes['deduplicated_into']} (already there at this revision)"
            if attributes and attributes.get("deduplicated_into")
            else f", {attributes['rows']} attribute row(s)" if attributes and attributes.get("ingested") else ""
        ),
        ", dry run" if dry_run else "",
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
