"""``clash_check_asset`` -- a clash check over a PUBLISHED ASSET NODE.

WHY A SECOND HANDLER RATHER THAN A BRANCH. ``clash_check`` is source-backed: the worker streams
``job.source_key`` to a local path before ``run()`` is called, and the handler dispatches on the
file's EXTENSION. Neither half fits here. The model comes from the provider that owns the format
(``ada.assets.concepts``), which reads the blobs it needs through the storage facade itself -- so a
pre-download would fetch the same export twice, and the extension would be one core has no reader
for anyway. Declaring this kind synthetic (``needs_source = False``) states that, and leaves the
file path untouched for every source core really does read.

WHAT IS ADDRESSED IS A SUBJECT, NOT A FILE. A published node's manifest names its provider and
carries the options that provider needs; the source may be one blob shared by many subjects, so
"the file" was never the right handle. The job therefore carries ``collection`` / ``subject`` /
``revision`` and the route resolves nothing about the format.

THE RESULT IS THE SAME DOCUMENT. Same identify, classify, match and group, same
``ada.clash/result@1`` blob at ``job.derived_key``, gzip-at-rest like every other derived JSON --
so the panel cannot tell this apart from a check over an IFC, which is the point. What it CAN tell
is where the model came from, because ``clash_check_from_asset_node`` stamps the provenance.
"""

from __future__ import annotations

import asyncio
import json
import traceback as tb_module

from ada.clash import ClashOptions
from ada.config import logger

from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job
from ..worker.audit import _audit_done
from .registry import JobContext, SyntheticFormatHandler

CLASH_CHECK_ASSET_KIND = "clash_check_asset"

__all__ = ["CLASH_CHECK_ASSET_KIND", "ClashCheckAssetHandler"]


def _options_from(raw: object) -> ClashOptions:
    return ClashOptions.from_dict(raw) if isinstance(raw, dict) else ClashOptions()


class ClashCheckAssetHandler(SyntheticFormatHandler):
    kind = CLASH_CHECK_ASSET_KIND

    async def run(self, job: Job, ctx: JobContext) -> None:
        from ada.clash.from_asset import clash_check_from_asset_node

        from ..worker.source_nodes import _SyncStorageFacade

        opts = job.conversion_options or {}
        collection = str(opts.get("collection") or "")
        subject = str(opts.get("subject") or "")
        if not collection or not subject:
            msg = f"clash_check_asset needs 'collection' and 'subject' in conversion_options " f"(got {sorted(opts)})"
            await ctx.queue.update(job.job_id, status=JOB_STATUS_ERROR, stage="clash", error=msg)
            await _audit_done(ctx.db_pool, job.job_id, "error", msg, ctx.started_at)
            return

        options = _options_from(opts.get("options"))
        revision = opts.get("revision") or None
        node = opts.get("node") or None

        loop = asyncio.get_running_loop()
        storage = _SyncStorageFacade(ctx.storage, ctx.scope, loop)

        def _check() -> dict:
            # The provider reads its own source through this same facade, which is why the
            # handler hands the facade over rather than any bytes: what it needs, and how much of
            # it, is the provider's business and core does not model it.
            return clash_check_from_asset_node(
                collection=collection,
                subject=subject,
                storage=storage,
                revision=revision,
                node=node,
                options=options,
            )

        try:
            await ctx.queue.update(job.job_id, stage="clash", progress=0.4)
            document = await loop.run_in_executor(None, _check)
        except Exception as exc:
            # Includes the two absences worth telling apart in the message rather than in the
            # status: no published manifest for the subject, and no reader for its provider in
            # THIS process -- the second means the check was routed to a pool that cannot serve
            # the format, which is a deployment fact and not a bad model.
            logger.exception("worker: clash_check_asset failed for job %s", job.job_id)
            trace = tb_module.format_exc()
            await ctx.queue.update(job.job_id, status=JOB_STATUS_ERROR, stage="clash", error=str(exc))
            await _audit_done(ctx.db_pool, job.job_id, "error", str(exc), ctx.started_at, traceback=trace)
            return

        try:
            await ctx.queue.update(job.job_id, stage="upload", progress=0.9)
            await ctx.storage.put_bytes(
                ctx.scope,
                job.derived_key,
                json.dumps(document).encode("utf-8"),
                content_encoding="gzip",
            )
        except Exception as exc:
            logger.exception("worker: clash_check_asset upload failed for job %s", job.job_id)
            trace = tb_module.format_exc()
            await ctx.queue.update(job.job_id, status=JOB_STATUS_ERROR, stage="upload", error=str(exc))
            await _audit_done(ctx.db_pool, job.job_id, "error", str(exc), ctx.started_at, traceback=trace)
            return

        await ctx.queue.update(job.job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
        await _audit_done(ctx.db_pool, job.job_id, "done", None, ctx.started_at)
