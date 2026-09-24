"""Identify, classify and group the joints in a model's SOURCE (``clash_check``) -- Decision 10's
identification half of the Clashes surface, run as an ordinary source-backed job.

INPUT IS THE SOURCE, NEVER THE GLB (see ``ada.clash.identify``'s own module docstring for why:
the GLB is triangles and every primitive ``ada.clash`` builds on takes a ``Beam``/``Plate``).
Reading that source into an ``ada`` Assembly is NOT reinvented here: every other source-backed job
already resolves a source key to a model through ``ada_load._load_with_ada`` (IFC / STEP /
Genie-XML / FEM / ACIS, dispatched by extension, sharing the same content-hashed parse cache every
audit conversion already benefits from). This handler is a :class:`.registry.SourceFormatHandler`
for exactly that reason -- the shared pre-step in ``worker/process.py`` downloads
``job.source_key`` to ``ctx.src_path`` BEFORE ``run`` is ever called, so a compiled procedural
model (whose derived artefact IS an IFC blob written by the structural build, same shape as any
other IFC source) needs no special case here at all: it is simply an ``.ifc`` key like any other.
"""

from __future__ import annotations

import asyncio
import hashlib
import pathlib
import traceback as tb_module

import asyncpg

from ada.cadit.ifc.read.native_members import load_members_or_model
from ada.clash import ClashOptions, run_clash_check
from ada.clash.builtin_specs import BUILTIN_SPEC_NAMES, register_builtin_specs
from ada.config import logger

from ..converters.ada_load import _load_with_ada
from ..converters.registry import UnsupportedFormat
from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job, JobQueue
from ..storage import Storage
from ..worker.audit import _audit_done
from .registry import JobContext, SourceFormatHandler

CLASH_CHECK_KIND = "clash_check"


def _options_from(raw: dict) -> ClashOptions:
    """Core's options and only core's -- see ``ada.clash.identify.ClashOptions``. Every field is
    normalised with the same defaults the dataclass declares, so a caller that sends ``{}`` and
    one that spells out the defaults produce the identical options object (and therefore the
    identical derived key the route composed before this job was even enqueued)."""
    return ClashOptions(
        out_of_plane_tol=float(raw.get("out_of_plane_tol", 0.1)),
        point_tol=float(raw.get("point_tol", 1e-5)),
        root=raw.get("root") or None,
        include_plate_joints=bool(raw.get("include_plate_joints", True)),
    )


async def _capability_of_factory(queue: "JobQueue"):
    """Resolve a registered spec's capability from the LIVE ``connection_specs`` heartbeat union
    -- never from the package that registered it (Decision 1's "capability, never provider"
    convention, reused unchanged by Decision 10).

    A built-in (``BUILTIN_SPEC_NAMES``) always answers ``None``: it runs wherever core runs, and
    no pool has to be online for it to be offered. Anything else answers whatever capability the
    live union currently advertises for that EXACT spec name, or ``None`` when no live pool is (or
    is no longer) advertising it -- ``ApplicableSpec.capability is None`` is documented as exactly
    the "registered somewhere, served by nobody right now" state the panel must show as
    unavailable rather than silently offer.
    """
    from ..routes.deps import live_worker_specs

    live = await live_worker_specs(queue, "connection_specs")

    def _capability_of(reg) -> str | None:
        if reg.spec.name in BUILTIN_SPEC_NAMES:
            return None
        entry = live.get(reg.spec.name)
        return (entry or {}).get("capability") if entry else None

    return _capability_of


def _adapy_version() -> str:
    try:
        import ada

        return getattr(ada, "__version__", "unknown")
    except Exception:  # noqa: BLE001 - provenance is best-effort, never a reason to fail the check
        return "unknown"


async def _run_clash_check(
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
    source_key = opts.get("source_key") or job.source_key

    try:
        options = _options_from(opts.get("options") or {})
    except (TypeError, ValueError) as exc:
        msg = f"bad clash-check options: {exc}"
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="clash", error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at)
        return

    ext = src_path.suffix.lower()
    loop = asyncio.get_running_loop()

    def _read_and_hash():
        # Members are all a check needs, and for an IFC they can be read without
        # ifcopenshell and without building any geometry (`load_members_or_model`).
        model = load_members_or_model(src_path, ext, _load_with_ada)
        # Computed from the DOWNLOADED bytes, here, where the full file already sits on local
        # disk -- the route that enqueued this job priced its derived key off a cheap key/e_tag
        # token instead (see routes/clash_check.py) precisely so it never has to fetch the whole
        # source just to answer a POST.
        sha256 = hashlib.sha256(src_path.read_bytes()).hexdigest()
        return model, sha256

    try:
        await queue.update(job_id, stage="loading", progress=0.15)
        model, source_sha256 = await loop.run_in_executor(None, _read_and_hash)
    except UnsupportedFormat as exc:
        msg = str(exc)
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="loading", error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at)
        return
    except Exception as exc:
        logger.exception("worker: clash_check failed to load %s for job %s", source_key, job_id)
        trace = tb_module.format_exc()
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="loading", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at, traceback=trace)
        return

    # Tolerant of an already-registered name (see its own docstring) -- called on every clash_check
    # run, not once at import time, because this is a fresh worker PROCESS as far as the spec
    # registry is concerned and the registry is process-global.
    register_builtin_specs()
    capability_of = await _capability_of_factory(queue)

    def _check():
        return run_clash_check(
            model,
            source_key=source_key,
            options=options,
            capability_of=capability_of,
            source_sha256=source_sha256,
            provenance={"adapy_version": _adapy_version()},
        )

    try:
        await queue.update(job_id, stage="clash", progress=0.55)
        result = await loop.run_in_executor(None, _check)
    except Exception as exc:
        logger.exception("worker: clash_check failed for job %s", job_id)
        trace = tb_module.format_exc()
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="clash", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at, traceback=trace)
        return

    try:
        await queue.update(job_id, stage="upload", progress=0.9)
        # gzip-at-rest, same as every other derived JSON blob: the presigned GET serves raw
        # bytes, so a derived document has to arrive already compressed (reference:
        # "Viewer GLB presigned-download needs gzip-at-rest").
        await storage.put_bytes(scope, job.derived_key, result.to_json(), content_encoding="gzip")
    except Exception as exc:
        logger.exception("worker: clash_check upload failed for job %s", job_id)
        trace = tb_module.format_exc()
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage="upload", error=str(exc))
        await _audit_done(db_pool, job_id, "error", str(exc), started_at, traceback=trace)
        return

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    await _audit_done(db_pool, job_id, "done", None, started_at)


class ClashCheckHandler(SourceFormatHandler):
    kind = CLASH_CHECK_KIND

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run_clash_check(
            job=job,
            src_path=ctx.src_path,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
        )
