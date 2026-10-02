"""``clash_check_group`` -- one clash check over a NAMED GROUP of members from several sources.

SYNTHETIC, LIKE ``clash_check_asset``, AND FOR A SHARPER REASON. There is no single source to
stream: the members may be files, published nodes, or both, from several collections and
providers. ``ada.clash.group_model.build_group_model`` reads each source through the storage facade
itself -- once per source, however many members it contributes -- and assembles ONE model, which is
the whole point: a joint between a member from one file and a member from another only exists once
both are in the same model.

THE RESULT IS THE SAME DOCUMENT. ``ada.clash/result@2`` at ``job.derived_key``, gzip-at-rest. Its
``source_key`` is ``group:<token>`` -- a group has no one source, and the prefix keeps anything from
trying to download it -- and ``provenance.group`` carries the normalised group with every node's
revision resolved, which is exactly what a detail job needs to rebuild the same model
(``formats/clash_detail.py``'s ``clash_detail_group``).
"""

from __future__ import annotations

import json
import pathlib
import shutil
import traceback as tb_module

from ada.clash import ClashOptions
from ada.config import logger

from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job
from ..worker.audit import _audit_done
from ..worker.settings import read_clash_timeout_s
from .clash_isolation import run_clash_work
from .registry import JobContext, SyntheticFormatHandler

CLASH_CHECK_GROUP_KIND = "clash_check_group"

__all__ = ["CLASH_CHECK_GROUP_KIND", "ClashCheckGroupHandler"]


class ClashCheckGroupHandler(SyntheticFormatHandler):
    kind = CLASH_CHECK_GROUP_KIND

    async def run(self, job: Job, ctx: JobContext) -> None:
        from ada.clash.group_model import clash_check_group

        from .clash_check import _adapy_version, _capability_of_factory

        opts = job.conversion_options or {}
        group = opts.get("group")
        token = str(opts.get("group_token") or "")
        if not isinstance(group, dict) or not group.get("members") or not token:
            msg = f"clash_check_group needs 'group' and 'group_token' in conversion_options (got {sorted(opts)})"
            await ctx.queue.update(job.job_id, status=JOB_STATUS_ERROR, stage="clash", error=msg)
            await _audit_done(ctx.db_pool, job.job_id, "error", msg, ctx.started_at)
            return

        try:
            options = ClashOptions.from_dict(opts.get("options") or {})
        except (TypeError, ValueError) as exc:
            msg = f"bad clash-check options: {exc}"
            await ctx.queue.update(job.job_id, status=JOB_STATUS_ERROR, stage="clash", error=msg)
            await _audit_done(ctx.db_pool, job.job_id, "error", msg, ctx.started_at)
            return

        # Resolved in the parent (it queries the live heartbeat union); the child gets the answers.
        capability_of = await _capability_of_factory(ctx.queue)
        provenance = {"adapy_version": _adapy_version()}

        def _work(out_dir: pathlib.Path, storage, progress) -> None:
            # Every member's source is read through the storage facade (served by the parent when
            # this runs in the killable child) -- once per source, see ``build_group_model``.
            progress("clash", 0.4)
            document = clash_check_group(
                group,
                token=token,
                options=options,
                storage=storage,
                capability_of=capability_of,
                provenance=provenance,
            )
            (out_dir / "result.json").write_bytes(json.dumps(document).encode("utf-8"))

        # A failure includes "no member resolved" (GroupModelError), which names what failed per member.
        out_dir = await run_clash_work(
            _work,
            job=job,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
            stage="clash",
            storage=ctx.storage,
            scope=ctx.scope,
            timeout_s=await read_clash_timeout_s(ctx.db_pool),
        )
        if out_dir is None:
            return  # cancelled / failed -- already reported

        try:
            await ctx.queue.update(job.job_id, stage="upload", progress=0.9)
            await ctx.storage.put_bytes(
                ctx.scope,
                job.derived_key,
                (out_dir / "result.json").read_bytes(),
                content_encoding="gzip",
            )
        except Exception as exc:
            logger.exception("worker: clash_check_group upload failed for job %s", job.job_id)
            trace = tb_module.format_exc()
            await ctx.queue.update(job.job_id, status=JOB_STATUS_ERROR, stage="upload", error=str(exc))
            await _audit_done(ctx.db_pool, job.job_id, "error", str(exc), ctx.started_at, traceback=trace)
            return
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

        await ctx.queue.update(job.job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
        await _audit_done(ctx.db_pool, job.job_id, "done", None, ctx.started_at)
