"""Chained external detailing stage (``procedural_detail``) and the relocation proposal
(``procedural_relocations``).
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import traceback as tb_module

import asyncpg

from ada.config import logger

from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job, JobQueue
from ..storage import Storage
from ..worker import state
from ..worker.audit import _audit_done
from ..worker.blobs import _wait_for_blob
from .procedural_support import (
    _assemble_compile_log,
    _capture_compile_logs,
    _compile_run_header,
    _prune_run_logs,
    _put_run_log,
    _put_run_pointer,
    _write_catalog_fp_sidecar,
)
from .registry import JobContext, SourceFormatHandler, SyntheticFormatHandler


async def _run_procedural_detail(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    """Chained EXTERNAL (Tier-B) detailing stage — the sibling of
    :func:`_run_procedural_build` that runs on a foreign capability pool.

    Reads the neutral structural artifact (IFC bytes) + its section sidecar the
    structural build wrote, resolves the external detailing engine's ``module:callable``
    entrypoint via :func:`ada.topo_model.engines.load_entrypoint` (the same mechanism
    the procedural engines use), and calls ``entrypoint(model_bytes, options)`` where
    ``options`` carries ``{"sections": <sidecar dict>, ...joint options}``. The returned
    detailing-layer GLB is written to ``job.derived_key`` gzip-at-rest."""
    import json as _json

    job_id = job.job_id
    opts = job.conversion_options or {}
    model_id = opts.get("model_id")
    entrypoint = opts.get("detailing_entrypoint")
    structural_ifc_key = opts.get("structural_ifc_key")
    structural_sections_key = opts.get("structural_sections_key")
    lod = "detail" if (opts.get("lod") or "sim") == "detail" else "sim"

    # This stage owns ``derived_key``, so it is the run the viewer's log lookup for
    # that artifact must find — same run identity (the job id) as the structural
    # stage, just a different key.
    run_log: dict[str, str] = {"key": "", "body": ""}

    async def _write_run_log(status: str, body: str | None = None) -> None:
        if body is not None:
            run_log["body"] = body
        header = _compile_run_header(
            run_id=job_id,
            model_id=model_id,
            revision=opts.get("revision"),
            engine=opts.get("engine"),
            lod=lod,
            detailing=opts.get("detailing"),
            is_preview=False,
            status=status,
        )
        text = f"{header}\n{run_log['body']}" if run_log["body"] else header
        run_log["key"] = await _put_run_log(storage, scope, model_id, job_id, text) or ""

    async def _fail(stage: str, msg: str, trace: str | None = None) -> None:
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage=stage, error=msg)
        await _write_run_log(f"failed at {stage}: {msg}")
        await _audit_done(
            db_pool,
            job_id,
            "error",
            msg,
            started_at,
            traceback=trace,
            metrics={"log_key": run_log["key"]} if run_log["key"] else None,
        )

    await _put_run_pointer(storage, scope, job.derived_key, job_id)

    if not entrypoint or ":" not in str(entrypoint):
        await _fail(
            "detail", "conversion_options.detailing_entrypoint (module:callable) is required for procedural_detail"
        )
        return
    if not structural_ifc_key:
        await _fail("detail", "conversion_options.structural_ifc_key is required for procedural_detail")
        return

    # The structural build runs on a DIFFERENT pool with NO ordering guarantee
    # relative to this job — this pool can claim procedural_detail before the
    # structural stage has written the neutral artifact. So WAIT (bounded) for the
    # IFC artifact to appear rather than failing on the first miss; only error if
    # it never shows within the budget (a genuinely failed / absent structural build).
    if not await _wait_for_blob(
        storage,
        scope,
        structural_ifc_key,
        queue=queue,
        job_id=job_id,
        budget_s=state.STRUCTURAL_ARTIFACT_WAIT_BUDGET_S,
        waiting_stage="waiting for structural build…",
    ):
        # A shutdown mid-wait leaves the job for redelivery (not a hard error);
        # a real timeout is a failure the operator should see.
        if state._WORKER_STOP is not None and state._WORKER_STOP.is_set():
            logger.info("worker: procedural_detail %s interrupted by shutdown while waiting", job_id)
            return
        await _fail(
            "fetch",
            f"structural artifact {structural_ifc_key!r} did not appear within "
            f"{state.STRUCTURAL_ARTIFACT_WAIT_BUDGET_S:.0f}s — the structural build may have failed",
        )
        return

    try:
        await queue.update(job_id, stage="fetch", progress=0.20)
        model_bytes = await storage.get_bytes(scope, structural_ifc_key)
    except Exception as exc:
        await _fail("fetch", f"structural artifact {structural_ifc_key!r} unreadable: {exc}")
        return

    sections: dict = {}
    if structural_sections_key:
        # The sidecar is written by the same build moments after the IFC; give it a
        # short grace to appear, then degrade to an empty sidecar (best-effort).
        await _wait_for_blob(
            storage,
            scope,
            structural_sections_key,
            queue=queue,
            job_id=job_id,
            budget_s=state.STRUCTURAL_SECTIONS_WAIT_BUDGET_S,
        )
        try:
            sections = _json.loads(await storage.get_bytes(scope, structural_sections_key))
        except Exception:
            logger.warning("procedural_detail: section sidecar %s unreadable; passing empty", structural_sections_key)

    from ada.topo_model.engines import load_entrypoint

    # Per-joint options selected in the UI ride on the job; the section sidecar is
    # merged in under a reserved key so the engine can guarantee section detection.
    options = {"sections": sections, "lod": lod}
    for k, v in (opts.get("detailing_options") or {}).items():
        options[k] = v

    loop = asyncio.get_running_loop()
    stdout_buf = io.StringIO()

    def _do_detail() -> bytes:
        fn = load_entrypoint(entrypoint)
        with contextlib.redirect_stdout(stdout_buf):
            return fn(model_bytes, options)

    with _capture_compile_logs() as log_handler:
        try:
            await queue.update(job_id, stage="detail", progress=0.50)
            glb_bytes = await loop.run_in_executor(None, _do_detail)
        except Exception as exc:
            logger.exception("worker: procedural_detail failed for %s", model_id)
            await _write_run_log(
                "failed at detail", _assemble_compile_log(log_handler, stdout_buf, tb_module.format_exc())
            )
            await _fail("detail", str(exc), tb_module.format_exc())
            return
        await _write_run_log("ok", _assemble_compile_log(log_handler, stdout_buf, None))

    try:
        await queue.update(job_id, stage="upload", progress=0.90)
        await storage.put_bytes(scope, job.derived_key, glb_bytes, content_encoding="gzip")
    except Exception as exc:
        logger.exception("worker: procedural_detail upload failed for %s", model_id)
        await _fail("upload", str(exc), tb_module.format_exc())
        return

    # The external detail output rides on the same catalog state as its structural
    # stage; stamp its own sidecar so the endpoint's staleness check on derived_key works.
    await _write_catalog_fp_sidecar(storage, scope, job.derived_key, job.conversion_options)

    await _prune_run_logs(storage, scope, model_id, run_log["key"])
    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    await _audit_done(
        db_pool,
        job_id,
        "done",
        None,
        started_at,
        metrics={"log_key": run_log["key"]} if run_log["key"] else None,
    )


async def _run_procedural_relocations(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    """Propose the minimum set of equipment relocations that make a procedural
    model's runs route cleanly (see :func:`ada.topo_model.relocate.propose_relocations`).

    A synthetic sibling of :func:`_run_procedural_build`: it reads the same
    postgres-stored doc (resolving placed catalog equipment by slug the same way)
    but produces a JSON *proposal* document rather than a GLB. The result is
    stored gzip-at-rest under the model's ``relocations.json`` derived key so the
    frontend can poll the job then fetch the blob. Relocations are proposals only —
    the worker never mutates the model."""
    import json

    job_id = job.job_id
    opts = job.conversion_options or {}
    model_id = opts.get("model_id")
    revision = opts.get("revision")

    async def _fail(stage: str, msg: str, trace: str | None = None) -> None:
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage=stage, error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at, traceback=trace)

    if not model_id or not isinstance(revision, int):
        await _fail("relocate", "conversion_options.model_id and revision are required for procedural_relocations")
        return
    if db_pool is None:
        await _fail("relocate", "procedural relocations require DATABASE_URL on the worker")
        return

    from .. import db as db_module

    row = await db_module.get_procedural_model(db_pool, model_id)
    if row is None:
        await _fail("relocate", f"procedural model {model_id} not found")
        return
    if row["revision"] != revision:
        await _fail(
            "relocate",
            f"procedural model {model_id} is at revision {row['revision']}, job requested r{revision} — "
            "re-trigger propose-relocations for the current revision",
        )
        return

    from ada.topo_model.relocate import propose_relocations

    # Resolve placed catalog equipment (by slug) to its per-scope definition, so a
    # candidate move keeps the equipment's real bbox/ports (matching the compile).
    catalog = await db_module.get_equipment_docs_by_scope(
        db_pool, scope_kind=row["scope_kind"], scope_id=row["scope_id"]
    )

    def _do_propose() -> bytes:
        result = propose_relocations(row["doc"], equipment_resolver=catalog.get)
        return json.dumps(result).encode("utf-8")

    loop = asyncio.get_running_loop()
    try:
        await queue.update(job_id, stage="relocate", progress=0.40)
        payload = await loop.run_in_executor(None, _do_propose)
    except Exception as exc:
        logger.exception("worker: procedural_relocations failed for %s", model_id)
        await _fail("relocate", str(exc), tb_module.format_exc())
        return

    try:
        await queue.update(job_id, stage="upload", progress=0.90)
        await storage.put_bytes(scope, job.derived_key, payload, content_encoding="gzip")
    except Exception as exc:
        logger.exception("worker: procedural_relocations upload failed for %s", model_id)
        await _fail("upload", str(exc), tb_module.format_exc())
        return

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    await _audit_done(db_pool, job_id, "done", None, started_at)


class ProceduralDetailHandler(SyntheticFormatHandler if not False else SourceFormatHandler):
    kind = "procedural_detail"

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run_procedural_detail(
            job=job,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
        )


class ProceduralRelocationsHandler(SyntheticFormatHandler if not False else SourceFormatHandler):
    kind = "procedural_relocations"

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run_procedural_relocations(
            job=job,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
        )
