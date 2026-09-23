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
"""

from __future__ import annotations

import asyncio
import hashlib
import pathlib
import traceback as tb_module

import asyncpg

from ada.clash import (
    ClashOptions,
    ClashResultError,
    describe_member,
    identify_joints,
    parse_clash_result,
)
from ada.clash.builtin_specs import register_builtin_specs
from ada.clash.match import detail_pairs
from ada.config import logger

from ..converters.ada_load import _load_with_ada
from ..converters.registry import UnsupportedFormat
from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job, JobQueue
from ..storage import Storage
from ..worker.audit import _audit_done
from .registry import JobContext, SourceFormatHandler

CLASH_DETAIL_KIND = "clash_detail"


def _found_id(found) -> str:
    """The exact formula ``run_clash_check`` stamps a joint's id with -- duplicated rather than
    imported because it is three lines of a documented, deterministic PUBLIC contract
    (``ada.clash.identify.run_clash_check``'s own docstring), not an internal of ``identify.py``:
    re-deriving it here is what lets a cached id be matched back up without ``ada.clash`` having
    to expose a second, id-taking entrypoint that exists for exactly one caller."""
    names = sorted(describe_member(m).name for m in found.members)
    return hashlib.sha256("|".join([found.origin, *names]).encode("utf-8")).hexdigest()[:12]


def _options_from(raw: dict) -> ClashOptions:
    return ClashOptions(
        out_of_plane_tol=float(raw.get("out_of_plane_tol", 0.1)),
        point_tol=float(raw.get("point_tol", 1e-5)),
        root=raw.get("root") or None,
        include_plate_joints=bool(raw.get("include_plate_joints", True)),
    )


async def _run_clash_detail(
    *,
    job: Job,
    src_path: pathlib.Path,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
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
        await _fail("detail", f"conversion_options missing {names} for a {CLASH_DETAIL_KIND} job")
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

    ext = src_path.suffix.lower()
    loop = asyncio.get_running_loop()

    def _load_and_find():
        model = _load_with_ada(src_path, ext)
        register_builtin_specs()
        outcome = identify_joints(model, options)
        return {_found_id(f): f for f in outcome.joints}

    try:
        await queue.update(job_id, stage="loading", progress=0.20)
        by_id = await loop.run_in_executor(None, _load_and_find)
    except UnsupportedFormat as exc:
        await _fail("loading", str(exc))
        return
    except Exception as exc:
        logger.exception("worker: clash_detail failed to reload %s for job %s", job.source_key, job_id)
        await _fail("loading", str(exc), tb_module.format_exc())
        return

    absent = [jid for jid in joint_ids if jid not in by_id]
    if absent:
        await _fail(
            "detail",
            "joint id(s) not reproducible from this source at these options: "
            f"{', '.join(absent)} -- the cached result and the source may have drifted apart",
        )
        return

    from ada.api.connections.spec import get_registered

    try:
        registered = get_registered(spec_name)
    except KeyError:
        await _fail(
            "detail",
            f"spec {spec_name!r} is not registered in this process -- it must be importable on "
            "the pool a clash_detail job for it is routed to",
        )
        return

    def _build() -> tuple[bytes, dict]:
        from ada import Part
        from ada.core.file_system import new_temp_path
        from ada.topo_model.takeoff import _joints_takeoff

        joints_part = Part("Joints")
        skipped: list[str] = []
        for jid in joint_ids:
            found = by_id[jid]
            # One connection per way the spec's roles bind at this contact -- a joint is a contact
            # NODE and three or four members can meet at one, while a builder takes a pair. See
            # `ada.clash.match.detail_pairs`, which the queue-less engine in `local_jobs` uses too:
            # the same job kind must not mean two different things depending on where it ran.
            try:
                pairs = detail_pairs(registered.spec, found)
            except ValueError as exc:
                skipped.append(f"{jid}: {exc}")
                continue
            for i, (landing, incoming) in enumerate(pairs):
                try:
                    conn = registered.fn(
                        landing=landing,
                        incoming=incoming,
                        centre=found.centre,
                        name=f"{spec_name}_{jid}" if i == 0 else f"{spec_name}_{jid}_{i}",
                        **gen_options,
                    )
                except Exception as exc:  # noqa: BLE001 - one joint's refusal is not the run's
                    # A builder's own prerequisites can be finer than a spec's criteria express;
                    # the other joints in the batch are still worth building. Only a run where
                    # NOTHING built is a failure -- see the queue-less engine, same rule.
                    skipped.append(f"{jid}: {type(exc).__name__}: {exc}")
                    continue
                joints_part.add_part(conn)
        if not joints_part.parts and skipped:
            raise ValueError(
                f"{spec_name} detailed none of the {len(joint_ids)} joint(s) handed to it: " + "; ".join(skipped[:5])
            )

        glb_path = new_temp_path(suffix=".glb")
        try:
            joints_part.to_gltf(glb_path)
            glb_bytes = glb_path.read_bytes()
        finally:
            glb_path.unlink(missing_ok=True)

        stats = {"joints": _joints_takeoff(joints_part)}
        if skipped:
            stats["skipped"] = skipped
        return glb_bytes, stats

    try:
        await queue.update(job_id, stage="detail", progress=0.55)
        glb_bytes, stats = await loop.run_in_executor(None, _build)
    except Exception as exc:
        logger.exception("worker: clash_detail build failed for job %s (spec %s)", job_id, spec_name)
        await _fail("detail", str(exc), tb_module.format_exc())
        return

    try:
        await queue.update(job_id, stage="upload", progress=0.85)
        import json as _json

        if glb_key:
            await storage.put_bytes(scope, glb_key, glb_bytes, content_encoding="gzip")
        await storage.put_bytes(scope, job.derived_key, _json.dumps(stats).encode("utf-8"), content_encoding="gzip")
    except Exception as exc:
        logger.exception("worker: clash_detail upload failed for job %s", job_id)
        await _fail("upload", str(exc), tb_module.format_exc())
        return

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    await _audit_done(db_pool, job_id, "done", None, started_at)


class ClashDetailHandler(SourceFormatHandler):
    kind = CLASH_DETAIL_KIND

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run_clash_detail(
            job=job,
            src_path=ctx.src_path,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
        )
