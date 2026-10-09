"""Re-derive named joints from a cached ``clash_check`` result and hand them to a registered
spec's builder (``clash_detail``) -- Decision 10's hand-off from identification to a generator.

SAME SOURCE, SAME OPTIONS, SAME IDS. A joint's id is a hash of its contact's member names and its
origin pass (``ada.clash.identify.run_clash_check``'s own documented contract), so re-running
``identify_joints`` against the same source with the same ``ClashOptions`` reproduces exactly the
ids the cached result carries -- nothing about a selection has to be stored between the two calls
beyond the ids themselves and the options that made them.

REAL MEMBERS, NOT DESCRIPTIONS. The cached result's ``JointMember`` rows are core-vocabulary
descriptions (kind / section family / member_type) -- enough to group and label a joint, never
enough to build with. A spec's registered function wants the actual ``Beam``/``Plate`` objects,
which exist only in a freshly-reloaded model, so this handler re-opens the SOURCE exactly the way
``clash_check`` does (:class:`.registry.SourceFormatHandler` -> the shared ``ada_load`` dispatch)
rather than trying to resurrect objects out of JSON.

A GROUP HAS NO SOURCE TO STREAM. A result over a named group (``provenance.group``) was checked
against a model assembled from several sources, so its detail job is the synthetic
``clash_detail_group`` kind: it rebuilds that model through ``ada.clash.group_model`` -- the same
function the check used, which is what makes the ids reproduce -- and then runs the same loop.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import traceback as tb_module
from typing import Any, Callable

import asyncpg

from ada.clash import ClashOptions, ClashResultError, parse_clash_result
from ada.clash.detail import build_detail, found_id, joints_by_id
from ada.config import logger

from ..converters.ada_load import _load_with_ada
from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job, JobQueue
from ..storage import Storage
from ..worker.audit import _audit_done
from ..worker.settings import read_clash_timeout_s
from .clash_isolation import run_clash_work
from .registry import JobContext, SourceFormatHandler, SyntheticFormatHandler

CLASH_DETAIL_KIND = "clash_detail"
CLASH_DETAIL_GROUP_KIND = "clash_detail_group"

#: Kept under its old name: the formula now lives in ``ada.clash.detail``, beside the loop that uses it.
_found_id = found_id


def _options_from(raw: dict) -> ClashOptions:
    # Every field, `checker` and `passes` included: a detail job re-runs identification to get
    # real members back, and a re-run with fewer passes or another checker reproduces other ids.
    return ClashOptions.from_dict(raw)


class ClashDetailError(ValueError):
    """A detail request the reloaded model cannot answer (its message is the whole story)."""


async def _run_clash_detail(
    *,
    job: Job,
    load_model: Callable[[Any, Any], Any],
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
    needs_storage: bool = False,
) -> None:
    """The detail job, whichever way its model is reopened. ``load_model(cached_result, storage)``
    runs in the killable child (a thread where there is no fork) and returns the model the cached
    result was checked against; ``storage`` is a synchronous facade when ``needs_storage``, else
    None."""
    job_id = job.job_id
    opts = job.conversion_options or {}
    result_key = opts.get("result_key")
    joint_ids = [str(j) for j in (opts.get("joint_ids") or [])]
    spec_name = opts.get("spec")
    gen_options = dict(opts.get("options") or {})
    glb_key = opts.get("glb_key")

    async def _fail(stage: str, msg: str, trace: str | None = None) -> None:
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage=stage, error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at, traceback=trace)

    missing_fields = [f for f in ("result_key", "spec") if not opts.get(f)]
    if missing_fields or not joint_ids:
        names = ", ".join(missing_fields or ["joint_ids"])
        await _fail("detail", f"conversion_options missing {names} for a {job.target_format} job")
        return

    try:
        raw = await storage.get_bytes(scope, result_key)
    except (FileNotFoundError, KeyError) as exc:
        await _fail("fetch", f"clash result {result_key!r} unreadable: {exc}")
        return
    try:
        cached = parse_clash_result(raw)
    except ClashResultError as exc:
        await _fail("fetch", f"{result_key}: {exc}")
        return
    if not cached.source_key:
        await _fail("fetch", f"{result_key}: clash result carries no source_key")
        return

    options = _options_from(cached.options)

    from ada.api.connections.spec import get_registered

    # Checked here, before any reload: a spec this pool cannot serve is a routing fact, and
    # saying so must not cost a model read. The registry is process-global, so the child that
    # builds sees the very same registration.
    try:
        registered = get_registered(spec_name)
    except KeyError:
        await _fail(
            "detail",
            f"spec {spec_name!r} is not registered in this process -- it must be importable on "
            "the pool a clash_detail job for it is routed to",
        )
        return

    def _work(out_dir: pathlib.Path, work_storage, progress) -> None:
        # Reload + re-identify + build + GLB export: all of it in the killable child
        # (``clash_isolation``). Outputs are files; the parent uploads them after a clean exit.
        progress("loading", 0.20)
        by_id = joints_by_id(load_model(cached, work_storage), options)
        progress("detail", 0.55)
        absent = [jid for jid in joint_ids if jid not in by_id]
        if absent:
            raise ClashDetailError(
                "joint id(s) not reproducible from this source at these options: "
                f"{', '.join(absent)} -- the cached result and the source may have drifted apart"
            )
        glb_bytes, stats = build_detail(
            registered, spec_name=spec_name, joint_ids=joint_ids, by_id=by_id, gen_options=gen_options
        )
        (out_dir / "detail.glb").write_bytes(glb_bytes)
        (out_dir / "stats.json").write_bytes(json.dumps(stats).encode("utf-8"))

    out_dir = await run_clash_work(
        _work,
        job=job,
        queue=queue,
        db_pool=db_pool,
        started_at=started_at,
        stage="loading",
        storage=storage if needs_storage else None,
        scope=scope,
        timeout_s=await read_clash_timeout_s(db_pool),
        label="clash detail",
    )
    if out_dir is None:
        return  # cancelled / failed -- already reported

    try:
        await queue.update(job_id, stage="upload", progress=0.85)
        if glb_key:
            await storage.put_path(scope, glb_key, out_dir / "detail.glb", content_encoding="gzip")
        await storage.put_bytes(scope, job.derived_key, (out_dir / "stats.json").read_bytes(), content_encoding="gzip")
    except Exception as exc:
        logger.exception("worker: clash_detail upload failed for job %s", job_id)
        await _fail("upload", str(exc), tb_module.format_exc())
        return
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    await _audit_done(db_pool, job_id, "done", None, started_at)


class ClashDetailHandler(SourceFormatHandler):
    kind = CLASH_DETAIL_KIND

    async def run(self, job: Job, ctx: JobContext) -> None:
        src_path: pathlib.Path = ctx.src_path

        def _load(_cached, _storage):
            return _load_with_ada(src_path, src_path.suffix.lower())

        await _run_clash_detail(
            job=job,
            load_model=_load,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
        )


class ClashDetailGroupHandler(SyntheticFormatHandler):
    """Detail joints from a result over a named GROUP (see the module docstring)."""

    kind = CLASH_DETAIL_GROUP_KIND

    async def run(self, job: Job, ctx: JobContext) -> None:
        from ada.clash.group_model import build_group_model

        def _load(cached, storage):
            group = cached.provenance.get("group")
            if not isinstance(group, dict):
                raise ClashResultError("this clash result was not checked over a group (no provenance.group)")
            return build_group_model(group, storage=storage).model

        await _run_clash_detail(
            job=job,
            load_model=_load,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
            needs_storage=True,
        )
