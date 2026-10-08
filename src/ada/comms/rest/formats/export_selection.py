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
import traceback as tb_module
from typing import Callable

from ada.config import logger
from ada.core.file_system import new_temp_path

from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job
from ..selection_export import (
    SELECTION_EXPORT_FORMATS,
    SelectionExportError,
    export_asset_selection,
    export_source_selection,
)
from ..worker.audit import _audit_done
from .registry import JobContext, SourceFormatHandler, SyntheticFormatHandler

EXPORT_SELECTION_KIND = "export_selection"
EXPORT_SELECTION_ASSET_KIND = "export_selection_asset"

__all__ = [
    "EXPORT_SELECTION_ASSET_KIND",
    "EXPORT_SELECTION_KIND",
    "ExportSelectionAssetHandler",
    "ExportSelectionHandler",
]


async def _fail(job: Job, ctx: JobContext, stage: str, msg: str, trace: str | None = None) -> None:
    await ctx.queue.update(job.job_id, status=JOB_STATUS_ERROR, stage=stage, error=msg)
    await _audit_done(ctx.db_pool, job.job_id, "error", msg, ctx.started_at, traceback=trace)


async def _export_and_upload(job: Job, ctx: JobContext, produce: Callable[[object], object]) -> None:
    """Run ``produce(out_path)`` off the loop, then store the file at the job's derived key."""
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

        def _produce(out_path):
            # The provider reads its own source through this facade -- what it needs, and how much,
            # is its business; core hands over the facade and never the bytes.
            return export_asset_selection(
                collection=collection,
                subject=subject,
                storage=storage,
                fmt=str(opts.get("format")),
                out_path=out_path,
                revision=opts.get("revision") or None,
                node=opts.get("node") or None,
                element=opts.get("element") or None,
                path=opts.get("path") or (),
            )

        await _export_and_upload(job, ctx, _produce)
