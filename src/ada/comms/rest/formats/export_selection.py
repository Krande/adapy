"""``export_selection`` / ``export_selection_asset`` -- one selected element of a loaded model, and
everything under it, written as a STEP or IFC file the viewer downloads.

TWO KINDS FOR THE SAME REASON THE CLASH CHECK HAS TWO. A file core reads is source-backed: the
worker streams ``job.source_key`` to a local path before ``run`` and the model is read from that
by extension (``ada_load._load_with_ada``, with its content-hashed parse cache). A published node
is synthetic: its model comes from the provider that owns the format (``ada.assets.concepts``),
which reads whatever blobs it needs through the storage facade itself -- a pre-download would
fetch the same export twice, and its extension is one core has no reader for anyway.

Everything past "here is a model" is shared (``ada.comms.rest.selection_export``): find the
selection by the name the tree showed, move it into a fresh assembly, write it. The file is
stored IDENTITY-encoded at ``job.derived_key`` -- not gzip-at-rest like a derived JSON -- so the
blob GET hands the browser a file it can save as it is, the same choice the procedural model
export makes for its ``.ifc``.
"""

from __future__ import annotations

import asyncio
import json
import traceback as tb_module
from typing import Callable

from ada.config import logger
from ada.core.file_system import new_temp_path

from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job
from ..selection_export import (
    EXPORT_LOG_SUFFIX,
    SELECTION_EXPORT_FORMATS,
    SelectionExportError,
    export_asset_selection,
    export_group_selection,
    export_source_selection,
    failure_log,
)
from ..worker.audit import _audit_done
from .registry import JobContext, SourceFormatHandler, SyntheticFormatHandler

EXPORT_SELECTION_KIND = "export_selection"
EXPORT_SELECTION_ASSET_KIND = "export_selection_asset"
EXPORT_SELECTION_GROUP_KIND = "export_selection_group"

__all__ = [
    "EXPORT_SELECTION_ASSET_KIND",
    "EXPORT_SELECTION_GROUP_KIND",
    "EXPORT_SELECTION_KIND",
    "ExportSelectionAssetHandler",
    "ExportSelectionGroupHandler",
    "ExportSelectionHandler",
]


def _attribute_lookup(ctx: JobContext):
    """The attribute lookup handed to a provider's concepts read, or None without a pool or API."""
    from ..worker.attribute_lookup import attribute_lookup_for

    try:
        return attribute_lookup_for(getattr(ctx, "db_pool", None), ctx.scope, asyncio.get_running_loop())
    except Exception:  # noqa: BLE001 - an export without attributes is still an export
        logger.warning("worker: no attribute lookup for this export", exc_info=True)
        return None


def _relation_lookup(ctx: JobContext):
    """The relation lookup an IFC is written with, or None without a pool or API."""
    from ..worker.relation_lookup import relation_lookup_for

    try:
        return relation_lookup_for(getattr(ctx, "db_pool", None), ctx.scope, asyncio.get_running_loop())
    except Exception:  # noqa: BLE001 - an export without relations is still an export
        logger.warning("worker: no relation lookup for this export", exc_info=True)
        return None


async def _fail(job: Job, ctx: JobContext, stage: str, msg: str, trace: str | None = None) -> None:
    await ctx.queue.update(job.job_id, status=JOB_STATUS_ERROR, stage=stage, error=msg)
    await _audit_done(ctx.db_pool, job.job_id, "error", msg, ctx.started_at, traceback=trace)


async def _export_and_upload(
    job: Job,
    ctx: JobContext,
    produce: Callable[[object], object],
    report: Callable[[], dict | None] | None = None,
) -> None:
    """Run ``produce(out_path)`` off the loop, then store the file at the job's derived key.

    ``report()``, when given and not None, is stored as JSON at ``<derived key>.log.json`` before
    the job is done, so a client that sees "done" can read it -- and also when ``produce`` fails
    with a :class:`SelectionExportError` carrying a ``detail``, with that ``detail`` as ``error``.
    """
    fmt = str((job.conversion_options or {}).get("format") or "")
    if fmt not in SELECTION_EXPORT_FORMATS:
        await _fail(
            job,
            ctx,
            "export",
            f"unsupported export format {fmt!r} (expected one of {sorted(SELECTION_EXPORT_FORMATS)})",
        )
        return

    out_path = new_temp_path(suffix=SELECTION_EXPORT_FORMATS[fmt])
    loop = asyncio.get_running_loop()
    try:
        try:
            await ctx.queue.update(job.job_id, stage="export", progress=0.3)
            await loop.run_in_executor(None, produce, out_path)
        except SelectionExportError as exc:
            # The selection's own problem -- not found, ambiguous, a source with no objects behind
            # its tree. A user reads this, so it goes out without a traceback.
            doc = failure_log(report() if report is not None else None, exc)
            if doc is not None:
                # Before the job reads as failed, so a client that sees "error" can read it.
                try:
                    await ctx.storage.put_bytes(
                        ctx.scope, f"{job.derived_key}{EXPORT_LOG_SUFFIX}", json.dumps(doc).encode("utf-8")
                    )
                except Exception:  # noqa: BLE001 - the sentence still says what failed
                    logger.warning("worker: could not write the failure log of job %s", job.job_id, exc_info=True)
            await _fail(job, ctx, "export", str(exc))
            return
        except Exception as exc:
            # Includes the provider's absences, named by `ada.assets.concepts`: no manifest for the
            # subject, or no concepts reader for its provider in THIS process.
            logger.exception("worker: %s failed for job %s", job.target_format, job.job_id)
            await _fail(job, ctx, "export", str(exc), tb_module.format_exc())
            return

        try:
            await ctx.queue.update(job.job_id, stage="upload", progress=0.9)
            # Streamed from disk: an export of a whole subject can be as large as its source.
            await ctx.storage.put_path(ctx.scope, job.derived_key, out_path)
            doc = report() if report is not None else None
            if doc is not None:
                await ctx.storage.put_bytes(
                    ctx.scope, f"{job.derived_key}{EXPORT_LOG_SUFFIX}", json.dumps(doc).encode("utf-8")
                )
        except Exception as exc:
            logger.exception("worker: %s upload failed for job %s", job.target_format, job.job_id)
            await _fail(job, ctx, "upload", str(exc), tb_module.format_exc())
            return
    finally:
        out_path.unlink(missing_ok=True)

    await ctx.queue.update(job.job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    await _audit_done(ctx.db_pool, job.job_id, "done", None, ctx.started_at)


class ExportSelectionHandler(SourceFormatHandler):
    kind = EXPORT_SELECTION_KIND

    async def run(self, job: Job, ctx: JobContext) -> None:
        opts = job.conversion_options or {}
        src_path = ctx.src_path

        def _produce(out_path):
            return export_source_selection(
                src_path,
                str(opts.get("format")),
                out_path,
                element=opts.get("element") or None,
                path=opts.get("path") or (),
                export_options=opts.get("export_options"),
            )

        await _export_and_upload(job, ctx, _produce)


class ExportSelectionAssetHandler(SyntheticFormatHandler):
    kind = EXPORT_SELECTION_ASSET_KIND

    async def run(self, job: Job, ctx: JobContext) -> None:
        from ..worker.source_nodes import _SyncStorageFacade

        opts = job.conversion_options or {}
        collection = str(opts.get("collection") or "")
        subject = str(opts.get("subject") or "")
        if not collection or not subject:
            await _fail(
                job,
                ctx,
                "export",
                f"{EXPORT_SELECTION_ASSET_KIND} needs 'collection' and 'subject' in conversion_options (got {sorted(opts)})",
            )
            return

        storage = _SyncStorageFacade(ctx.storage, ctx.scope, asyncio.get_running_loop())
        attributes = _attribute_lookup(ctx)
        relations = _relation_lookup(ctx)

        def _produce(out_path):
            # The provider reads its own source through this facade -- what it needs, and how much,
            # is its business; core hands over the facade and never the bytes.
            return export_asset_selection(
                attributes=attributes,
                relations=relations,
                collection=collection,
                subject=subject,
                storage=storage,
                fmt=str(opts.get("format")),
                out_path=out_path,
                revision=opts.get("revision") or None,
                node=opts.get("node") or None,
                element=opts.get("element") or None,
                path=opts.get("path") or (),
                export_options=opts.get("export_options"),
            )

        await _export_and_upload(job, ctx, _produce)


class ExportSelectionGroupHandler(SyntheticFormatHandler):
    """Several elements, from one or several models, written as one file. Synthetic for the reason
    ``clash_check_group`` is: the members' sources are read through the storage facade, each once."""

    kind = EXPORT_SELECTION_GROUP_KIND

    async def run(self, job: Job, ctx: JobContext) -> None:
        from ..worker.source_nodes import _SyncStorageFacade

        opts = job.conversion_options or {}
        group = opts.get("group")
        if not isinstance(group, dict) or not group.get("members"):
            await _fail(job, ctx, "export", f"{EXPORT_SELECTION_GROUP_KIND} needs 'group' in conversion_options")
            return

        storage = _SyncStorageFacade(ctx.storage, ctx.scope, asyncio.get_running_loop())
        attributes = _attribute_lookup(ctx)
        relations = _relation_lookup(ctx)
        geometry_provider = opts.get("geometry_provider") or None
        warnings: list[str] = []

        def _produce(out_path):
            warnings.extend(
                export_group_selection(
                    group,
                    attributes=attributes,
                    relations=relations,
                    storage=storage,
                    fmt=str(opts.get("format")),
                    out_path=out_path,
                    geometry_provider=geometry_provider,
                    export_options=opts.get("export_options"),
                )
            )

        def _report() -> dict:
            return {"geometry_provider": geometry_provider, "members": len(group["members"]), "warnings": warnings}

        await _export_and_upload(job, ctx, _produce, _report)
