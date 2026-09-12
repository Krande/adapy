"""Equipment bbox inference from a linked CAD asset (``equipment_bbox``).
"""

from __future__ import annotations

import asyncio
import pathlib
import traceback as tb_module

import asyncpg

from ada.config import logger

from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job, JobQueue
from ..storage import Storage
from ..worker.audit import _audit_done
from .registry import JobContext, SourceFormatHandler, SyntheticFormatHandler


def _infer_equipment_geometry(data: bytes, ext: str, z_up: bool = True) -> tuple[dict, bytes]:
    """Read a CAD/mesh asset, returning its axis-aligned bounding-box extents
    ``{lx, ly, lz}`` (in metres) and a preview GLB for the sidecar viewer. Mesh
    formats load via trimesh; CAD formats via the matching ada reader.

    ``z_up=True`` (default) takes the asset as authored in adapy's **Z-up**
    convention (ada readers and ada-exported GLBs are Z-up): ``lz`` = the Z extent
    = height and the mesh is NOT re-oriented — measuring/previewing it verbatim
    keeps lz == the CAD's real vertical extent. ``z_up=False`` treats a mesh asset
    (.glb/.gltf/.stl/.obj) as glTF-spec **Y-up** and re-orients it Y-up→Z-up
    (rotate +90° about X) before measuring and previewing, so the inferred bbox
    and the preview GLB are both in adapy's Z-up frame. ``z_up`` is ignored for
    ada-reader formats (already Z-up)."""
    import pathlib as _pl
    import tempfile as _tf

    ext = ext.lower()
    with _tf.TemporaryDirectory(prefix="eqbbox_") as tmp:
        src = _pl.Path(tmp) / f"source{ext}"
        src.write_bytes(data)
        if ext in (".glb", ".gltf", ".stl", ".obj"):
            import numpy as np
            import trimesh

            scene = trimesh.load(src, force="scene")
            if not z_up:
                # Re-orient a Y-up authored mesh into adapy's Z-up frame; both the
                # measured bounds and the exported preview then match Z-up.
                scene.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
            bounds = scene.bounds
            if z_up and ext in (".glb", ".gltf"):
                preview = data
            else:
                preview = scene.export(file_type="glb")
        else:
            import ada

            readers = {
                ".step": ada.from_step,
                ".stp": ada.from_step,
                ".ifc": ada.from_ifc,
                ".sat": ada.from_acis,
                ".xml": ada.from_genie_xml,
            }
            reader = readers.get(ext)
            if reader is None:
                raise ValueError(f"unsupported CAD extension {ext!r} for bbox inference")
            a = reader(src)
            bounds = a.to_trimesh_scene().bounds
            out = _pl.Path(tmp) / "preview.glb"
            a.to_gltf(out)
            preview = out.read_bytes()

    if bounds is None:
        raise ValueError("could not determine geometry bounds (empty model?)")
    lo, hi = bounds[0], bounds[1]
    bbox = {"lx": float(hi[0] - lo[0]), "ly": float(hi[1] - lo[1]), "lz": float(hi[2] - lo[2])}
    return bbox, preview


def _load_cad_mesh(data: bytes, ext: str, z_up: bool = True):
    """Load a CAD/mesh asset into a single concatenated trimesh (graph
    transforms baked). Used to splice real equipment geometry into a compiled
    procedural model.

    ``z_up=True`` (default) takes the asset verbatim (adapy Z-up convention).
    ``z_up=False`` re-orients a mesh asset (.glb/.gltf/.stl/.obj) from glTF-spec
    Y-up into Z-up (rotate +90° about X) before baking, so the spliced geometry
    lands in the same frame as the inferred bbox. Ignored for ada-reader formats
    (already Z-up)."""
    import pathlib as _pl
    import tempfile as _tf

    import numpy as np
    import trimesh

    ext = ext.lower()
    with _tf.TemporaryDirectory(prefix="eqcad_") as tmp:
        src = _pl.Path(tmp) / f"source{ext}"
        src.write_bytes(data)
        if ext in (".glb", ".gltf", ".stl", ".obj"):
            scene = trimesh.load(src, force="scene")
            if not z_up:
                scene.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0]))
        else:
            import ada

            readers = {
                ".step": ada.from_step,
                ".stp": ada.from_step,
                ".ifc": ada.from_ifc,
                ".sat": ada.from_acis,
                ".xml": ada.from_genie_xml,
            }
            reader = readers.get(ext)
            if reader is None:
                raise ValueError(f"unsupported CAD extension {ext!r} for geometry splice")
            scene = reader(src).to_trimesh_scene()
    return scene.dump(concatenate=True)


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
