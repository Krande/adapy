"""Procedural model export/import (``procedural_export_xlsx`` / ``procedural_export_model`` /
``procedural_import_xlsx``).
"""

from __future__ import annotations

import asyncio
import pathlib
import traceback as tb_module

import asyncpg

from ada.config import logger

from .. import db as db_module
from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job, JobQueue
from ..storage import Storage
from ..worker.audit import _audit_done
from .equipment import _load_cad_mesh
from .procedural_support import (
    _advertised_engine_doc,
    _resolve_engine_manifest,
    _write_catalog_fp_sidecar,
)
from .registry import JobContext, SourceFormatHandler, SyntheticFormatHandler


async def _run_procedural_export_xlsx(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    """Export a procedural model (postgres-stored doc) to its engine's Excel
    workbook (bytes), stamped with the ``_ADA_META`` sheet, and store it at
    ``job.derived_key`` (an ``.xlsx`` blob the frontend downloads). A synthetic
    sibling of :func:`_run_procedural_build`, routed to the engine's capability."""
    job_id = job.job_id
    opts = job.conversion_options or {}
    model_id = opts.get("model_id")
    revision = opts.get("revision")
    engine = opts.get("engine")

    async def _fail(stage: str, msg: str, trace: str | None = None) -> None:
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage=stage, error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at, traceback=trace)

    if not model_id or not isinstance(revision, int):
        await _fail("export", "conversion_options.model_id and revision are required for procedural_export_xlsx")
        return
    if db_pool is None:
        await _fail("export", "procedural export requires DATABASE_URL on the worker")
        return

    row = await db_module.get_procedural_model(db_pool, model_id)
    if row is None:
        await _fail("export", f"procedural model {model_id} not found")
        return
    if row["revision"] != revision:
        await _fail(
            "export",
            f"procedural model {model_id} is at revision {row['revision']}, job requested r{revision}",
        )
        return

    manifest_doc = await _resolve_engine_manifest(db_pool, row, engine)
    doc = row["doc"]

    from ada.topo_model.engines import EngineHasNoExcelFormat, export_doc_to_xlsx

    def _do_export() -> bytes:
        return export_doc_to_xlsx(engine, doc, name=row["name"], manifest_doc=manifest_doc)

    loop = asyncio.get_running_loop()
    try:
        await queue.update(job_id, stage="export", progress=0.40)
        xlsx_bytes = await loop.run_in_executor(None, _do_export)
    except EngineHasNoExcelFormat as exc:
        await _fail("export", str(exc))
        return
    except Exception as exc:
        logger.exception("worker: procedural_export_xlsx failed for %s", model_id)
        await _fail("export", str(exc), tb_module.format_exc())
        return

    try:
        await queue.update(job_id, stage="upload", progress=0.90)
        # An xlsx is an already-compressed zip — store identity (no gzip re-encode),
        # so the presigned/blob GET hands the browser a valid .xlsx download.
        await storage.put_bytes(scope, job.derived_key, xlsx_bytes)
    except Exception as exc:
        logger.exception("worker: procedural_export_xlsx upload failed for %s", model_id)
        await _fail("upload", str(exc), tb_module.format_exc())
        return

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    await _audit_done(db_pool, job_id, "done", None, started_at)


async def _run_procedural_export_model(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    """Export a procedural model to a downloadable CAD/analysis file: ``ifc`` (the
    DETAIL model — the clash cuts ride along as IfcRelVoidsElement voids, equipment
    as IfcPump/IfcTank/…), ``gxml`` (the SIMULATION model as a Genie concept XML) or
    ``gnx`` (that XML as a Genie workspace).

    Compiles the postgres-stored doc to an in-process adapy assembly (built-in
    engine only) at the format's LOD, serializes it, and stores the bytes at
    ``job.derived_key``. A synthetic sibling of :func:`_run_procedural_export_xlsx`."""
    job_id = job.job_id
    opts = job.conversion_options or {}
    model_id = opts.get("model_id")
    revision = opts.get("revision")
    export_format = (opts.get("export_format") or "").lower()
    lod = "detail" if (opts.get("lod") or "sim") == "detail" else "sim"
    detailing = opts.get("detailing")
    # IFC only: splice real catalog CAD geometry for equipment (default on). The
    # Genie export keeps equipment as its concept type (prism_shape), so it never
    # splices CAD — equipment there stays an ada.Equipment carrying mass/footprint.
    cad_equipment = export_format == "ifc" and bool(opts.get("cad_equipment", True))

    async def _fail(stage: str, msg: str, trace: str | None = None) -> None:
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage=stage, error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at, traceback=trace)

    if not model_id or not isinstance(revision, int):
        await _fail("export", "conversion_options.model_id and revision are required for procedural_export_model")
        return
    if export_format not in ("ifc", "gxml", "gnx"):
        await _fail("export", f"unsupported export_format {export_format!r} (expected ifc, gxml or gnx)")
        return
    if db_pool is None:
        await _fail("export", "procedural export requires DATABASE_URL on the worker")
        return

    row = await db_module.get_procedural_model(db_pool, model_id)
    if row is None:
        await _fail("export", f"procedural model {model_id} not found")
        return
    if row["revision"] != revision:
        await _fail(
            "export", f"procedural model {model_id} is at revision {row['revision']}, job requested r{revision}"
        )
        return

    doc = row["doc"]
    name = row["name"]

    # Resolve placed catalog equipment (by slug) to its per-scope definition so the
    # equipment is faithful — an ada.Equipment with the catalog's bbox/mass/ports/IFC
    # class (IfcPump/IfcTank/…) and a Genie prism_shape — instead of an anonymous box.
    catalog = await db_module.get_equipment_docs_by_scope(
        db_pool, scope_kind=row["scope_kind"], scope_id=row["scope_id"]
    )
    # IFC + CAD-on: prefetch the linked CAD assets for the placed slugs so the
    # compiler can splice real geometry in place of the placeholder box body.
    cad_bytes: dict[str, tuple[bytes, str, bool]] = {}
    if cad_equipment:
        used = {(e.get("DESCRIPTION") or "").strip() for e in (doc.get("equipments") or [])}
        cad_keys = await db_module.get_equipment_cad_keys_by_scope(
            db_pool, scope_kind=row["scope_kind"], scope_id=row["scope_id"]
        )
        for slug, cad_key in cad_keys.items():
            if slug and slug in used and cad_key:
                try:
                    data = await storage.get_bytes(scope, cad_key)
                    z_up = bool((catalog.get(slug) or {}).get("cad_z_up", True))
                    cad_bytes[slug] = (data, pathlib.PurePosixPath(cad_key).suffix.lower(), z_up)
                except Exception:
                    logger.warning("procedural export: CAD asset %s for %r unreadable; using box", cad_key, slug)

    def _do_export() -> bytes:
        import os
        import tempfile

        from ada.topo_model.compile import build_procedural_assembly

        # Splice CAD only when asked (IFC). ``equipment_cad`` on the doc drives the
        # compiler's box-vs-CAD choice; force it to match this export's option so a
        # download reflects the toggle rather than the model's stored preference.
        export_doc = {**doc, "equipment_cad": bool(cad_equipment and cad_bytes)}
        cad_meshes: dict[str, object] = {}
        for slug, (data, ext, z_up) in cad_bytes.items():
            try:
                cad_meshes[slug] = _load_cad_mesh(data, ext, z_up=z_up)
            except Exception:
                logger.warning("procedural export: failed to load CAD mesh for %r; using box", slug)

        # Built-in engine only: build the in-process ada.Assembly the IFC / Genie
        # writers need (no GLB — this path never tessellates). The equipment resolver
        # makes catalog equipment faithful; cad_as_objects materialises resolved CAD
        # equipment as real assembly geometry (IfcTriangulatedFaceSet) rather than a
        # GLB-only splice, so it serializes into the IFC.
        asm = build_procedural_assembly(
            export_doc,
            name=name,
            lod=lod,
            detailing=detailing if export_format == "ifc" else None,
            equipment_resolver=catalog.get,
            cad_scene_resolver=cad_meshes.get if cad_meshes else None,
            cad_as_objects=bool(cad_meshes),
        )
        with tempfile.TemporaryDirectory() as d:
            if export_format == "ifc":
                p = os.path.join(d, "model.ifc")
                asm.to_ifc(p, file_obj_only=False)
            else:
                p = os.path.join(d, "model.gxml")
                # Equipment defaults to AS_IS (which the Genie writer skips); promote
                # each to FOOTPRINT_MASS so it exports as a Genie equipment concept
                # (prism_shape + placed load) rather than being dropped.
                from ada.api.spatial.eq_types import EquipRepr
                from ada.api.spatial.equipment import Equipment

                for part in asm.get_all_parts_in_assembly(include_self=True):
                    if isinstance(part, Equipment) and part.eq_repr == EquipRepr.AS_IS:
                        part.eq_repr = EquipRepr.FOOTPRINT_MASS
                # embed_sat=False keeps the export CAD-backend-independent (plates as
                # polygons; Genie rebuilds the ACIS on import).
                asm.to_genie_xml(p, embed_sat=False)
                if export_format == "gnx":
                    # Repack that XML as a workspace rather than calling to_gnx(), which
                    # builds the ACIS body through the CAD backend: a polygon XML gets
                    # the empty-body SAT and Genie builds the body from the polygons on
                    # load, exactly as when the XML is imported by hand.
                    from ada.cadit.gxml.write.write_gnx import gnx_from_genie_xml

                    p = str(gnx_from_genie_xml(p, os.path.join(d, "model.gnx")))
            with open(p, "rb") as fh:
                return fh.read()

    loop = asyncio.get_running_loop()
    try:
        await queue.update(job_id, stage="export", progress=0.40)
        data = await loop.run_in_executor(None, _do_export)
    except Exception as exc:
        logger.exception("worker: procedural_export_model (%s) failed for %s", export_format, model_id)
        await _fail("export", str(exc), tb_module.format_exc())
        return

    try:
        await queue.update(job_id, stage="upload", progress=0.90)
        # Store identity (not gzip-at-rest) so the presigned/blob GET hands the
        # browser a directly-usable .ifc / .gxml text file.
        await storage.put_bytes(scope, job.derived_key, data)
    except Exception as exc:
        logger.exception("worker: procedural_export_model upload failed for %s", model_id)
        await _fail("upload", str(exc), tb_module.format_exc())
        return

    # Bind the export to the catalog state it resolved equipment from.
    await _write_catalog_fp_sidecar(storage, scope, job.derived_key, job.conversion_options)

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    await _audit_done(db_pool, job_id, "done", None, started_at)


async def _run_procedural_import_xlsx(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    """Import an uploaded Excel workbook into a NEW procedural model.

    ``conversion_options`` carries ``{source_key, engine, name, created_by}``; the
    engine (chosen from the ``_ADA_META`` sheet or the user's prompt) parses the
    workbook into a procedural document, which is committed as a fresh model. The
    original workbook is kept as the model's ``source_xlsx_key`` (full-fidelity
    source). A small JSON result ``{model_id, name, engine, revision}`` is written
    to ``job.derived_key`` so the frontend can open the new model."""
    import json

    job_id = job.job_id
    opts = job.conversion_options or {}
    source_key = opts.get("source_key")
    engine = opts.get("engine")
    name = (opts.get("name") or "").strip() or "Imported model"
    created_by = opts.get("created_by")

    async def _fail(stage: str, msg: str, trace: str | None = None) -> None:
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage=stage, error=msg)
        await _audit_done(db_pool, job_id, "error", msg, started_at, traceback=trace)

    if not source_key:
        await _fail("import", "conversion_options.source_key is required for procedural_import_xlsx")
        return
    if db_pool is None:
        await _fail("import", "procedural import requires DATABASE_URL on the worker")
        return

    try:
        xlsx_bytes = await storage.get_bytes(scope, source_key)
    except Exception as exc:
        await _fail("import", f"uploaded workbook {source_key!r} unreadable: {exc}")
        return

    # A NON-default, non-builtin engine's manifest is resolved by slug in this
    # scope (mirrors export/build) — needed to locate its import entrypoint.
    from ada.topo_model.engines import BUILTIN_ENGINES, is_default_engine

    manifest_doc = None
    if not is_default_engine(engine) and engine not in BUILTIN_ENGINES:
        eng_row = await db_module.get_procedural_engine_by_slug(
            db_pool, scope_kind=scope.kind, scope_id=scope.id, slug=engine
        )
        manifest_doc = ((eng_row or {}).get("doc") if eng_row else None) or _advertised_engine_doc(engine)
        if manifest_doc is None:
            await _fail("import", f"procedural engine {engine!r} is neither registered in scope nor advertised here")
            return

    from ada.comms.rest.procedural import procedural_source_key, validate_doc
    from ada.topo_model.engines import (
        DEFAULT_ENGINE_SLUG,
        EngineHasNoExcelFormat,
        import_xlsx_to_doc,
    )

    def _do_import() -> dict:
        parsed = import_xlsx_to_doc(engine, xlsx_bytes, manifest_doc=manifest_doc)
        # Stamp the routing header so a subsequent compile auto-routes back to this
        # engine, then validate/normalize through the same path the commit uses.
        parsed["engine"] = engine or DEFAULT_ENGINE_SLUG
        return validate_doc(parsed)

    loop = asyncio.get_running_loop()
    try:
        await queue.update(job_id, stage="import", progress=0.40)
        doc = await loop.run_in_executor(None, _do_import)
    except EngineHasNoExcelFormat as exc:
        await _fail("import", str(exc))
        return
    except Exception as exc:
        logger.exception("worker: procedural_import_xlsx parse failed for %s", source_key)
        await _fail("import", str(exc), tb_module.format_exc())
        return

    # Create the model row, then stash the original workbook as its full-fidelity
    # source and commit the parsed doc (revision 0 -> 1).
    model_row = await db_module.create_procedural_model(
        db_pool, scope_kind=scope.kind, scope_id=scope.id, name=name, created_by=created_by
    )
    if model_row is None:
        await _fail("import", f"a procedural model named {name!r} already exists in this scope")
        return
    model_id = model_row["id"]

    src_key = procedural_source_key(model_id)
    try:
        await storage.put_bytes(scope, src_key, xlsx_bytes)
        doc["source_xlsx_key"] = src_key
    except Exception:
        logger.warning("worker: import could not stash source workbook for %s", model_id)

    new_rev = await db_module.update_procedural_model_doc(db_pool, model_id, doc, model_row["revision"])
    if new_rev is None:
        await _fail("import", f"failed to commit imported doc for model {model_id}")
        return

    payload = json.dumps({"model_id": model_id, "name": name, "engine": doc.get("engine"), "revision": new_rev}).encode(
        "utf-8"
    )
    try:
        await queue.update(job_id, stage="upload", progress=0.90)
        await storage.put_bytes(scope, job.derived_key, payload, content_encoding="gzip")
    except Exception as exc:
        logger.exception("worker: procedural_import_xlsx result upload failed for %s", model_id)
        await _fail("upload", str(exc), tb_module.format_exc())
        return

    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    await _audit_done(db_pool, job_id, "done", None, started_at)


class ProceduralExportXlsxHandler(SyntheticFormatHandler if not False else SourceFormatHandler):
    kind = "procedural_export_xlsx"

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run_procedural_export_xlsx(
            job=job,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
        )


class ProceduralExportModelHandler(SyntheticFormatHandler if not False else SourceFormatHandler):
    kind = "procedural_export_model"

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run_procedural_export_model(
            job=job,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
        )


class ProceduralImportXlsxHandler(SyntheticFormatHandler if not False else SourceFormatHandler):
    kind = "procedural_import_xlsx"

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run_procedural_import_xlsx(
            job=job,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
        )
