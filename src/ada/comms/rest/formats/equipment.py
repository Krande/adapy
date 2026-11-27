"""Equipment bbox inference from a linked CAD asset (``equipment_bbox``)."""

from __future__ import annotations

import asyncio
import pathlib
import traceback as tb_module

import asyncpg

from ada.config import logger

from ..equipment_geometry import infer_equipment_geometry, load_cad_mesh
from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job, JobQueue
from ..storage import Storage
from ..worker.audit import _audit_done
from .registry import JobContext, SourceFormatHandler, SyntheticFormatHandler


# The pure work lives in ``equipment_geometry`` (the browser runs it too); the old names stay.
_infer_equipment_geometry = infer_equipment_geometry
_load_cad_mesh = load_cad_mesh


async def _run_equipment_bbox(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    """Infer an equipment type's bounding box from its linked CAD asset and
    render a preview GLB. ``conversion_options`` carries ``{"type_id", "cad_key"}``;
    the inferred bbox is merged into the equipment doc (no revision bump) and the
    preview lands at ``job.derived_key`` (``_equipment/{id}/preview.glb``)."""
    job_id = job.job_id
    opts = job.conversion_options or {}
    type_id = opts.get("type_id")
    cad_key = opts.get("cad_key")
    # Whether the CAD asset is authored Z-up (adapy convention). Default True =
    # verbatim; False re-orients a Y-up mesh into Z-up before measuring.
    cad_z_up = bool(opts.get("cad_z_up", True))

    async def _fail(stage: str, msg: str, trace: str | None = None) -> None:
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage=stage, error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at, traceback=trace)

    if not type_id or not cad_key:
        await _fail("build", "conversion_options.type_id and cad_key are required for equipment_bbox")
        return
    if db_pool is None:
        await _fail("build", "equipment bbox inference requires DATABASE_URL on the worker")
        return

    from .. import db as db_module

    try:
        data = await storage.get_bytes(scope, cad_key)
    except Exception as exc:
        await _fail("read", f"CAD asset {cad_key} not readable: {exc}")
        return

    ext = pathlib.PurePosixPath(cad_key).suffix.lower()
    loop = asyncio.get_running_loop()
    try:
        await queue.update(job_id, stage="build", progress=0.40)
        bbox, preview = await loop.run_in_executor(None, lambda: _infer_equipment_geometry(data, ext, z_up=cad_z_up))
    except Exception as exc:
        logger.exception("worker: equipment_bbox failed for %s", type_id)
        await _fail("build", str(exc), tb_module.format_exc())
        return

    try:
        await queue.update(job_id, stage="upload", progress=0.90)
        await storage.put_bytes(scope, job.derived_key, preview, content_encoding="gzip")
        await db_module.apply_inferred_bbox(db_pool, type_id, bbox)
    except Exception as exc:
        logger.exception("worker: equipment_bbox upload failed for %s", type_id)
        await _fail("upload", str(exc), tb_module.format_exc())
        return

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    await _audit_done(db_pool, job_id, "done", None, started_at)


class EquipmentBboxHandler(SyntheticFormatHandler if not False else SourceFormatHandler):
    kind = "equipment_bbox"
    skip_cached_short_circuit = True

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run_equipment_bbox(
            job=job,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
        )
