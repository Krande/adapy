"""Procedural structural build (``procedural_build``): compile the postgres-stored model doc to a
GLB, with the captured compile log, run-log pointers and catalog fingerprint sidecar.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import pathlib
import tempfile
import traceback as tb_module

import asyncpg

from ada.config import logger

from ..queue import JOB_STATUS_DONE, JOB_STATUS_ERROR, Job, JobQueue
from ..storage import Storage
from ..worker.audit import _audit_done
from .equipment import _load_cad_mesh
from .procedural_support import (
    _advertised_engine_doc,
    _assemble_compile_log,
    _capture_compile_logs,
    _compile_run_header,
    _prune_run_logs,
    _put_run_log,
    _put_run_pointer,
    _write_catalog_fp_sidecar,
)
from .registry import JobContext, SourceFormatHandler, SyntheticFormatHandler


async def _run_procedural_build(
    *,
    job: Job,
    scope,
    storage: "Storage",
    queue: "JobQueue",
    db_pool: "asyncpg.Pool | None",
    started_at: float,
) -> None:
    """Compile a procedural cell model (postgres-stored doc) into a GLB.

    ``conversion_options`` carries ``{"model_id": ..., "revision": ...}``; the
    worker reads the doc straight from postgres (single source of truth) and
    errors on a revision mismatch so the revision-stamped derived_key always
    matches its content. The compile runs in-process via
    ``ada.topo_model.compile`` (pure adapy + tessellation)."""
    job_id = job.job_id
    opts = job.conversion_options or {}
    model_id = opts.get("model_id")
    revision = opts.get("revision")
    lod = "detail" if (opts.get("lod") or "sim") == "detail" else "sim"
    # Selected procedural engine (None / "adapy-default" = the built-in compile).
    engine = opts.get("engine")
    # Selected DETAILING engine — a fabrication-detail stage run in-process as
    # stage 2 of this same job, between the structural build and to_glb() (see
    # ada.topo_model.detailing). None/"none" = no detailing (byte-identical to the
    # plain structural build). Only the in-process builtin (adapy-default) is
    # applied here; an external (Tier-B) engine is a chained capability job (Phase 2).
    detailing = opts.get("detailing")
    # Per-joint-type detailing options (the Detailing tab's toggles + field values,
    # keyed by joint slug) threaded into the in-process detail() so a knob change
    # (weld leg, plate thickness, overhang, clearance …) actually alters geometry.
    detailing_options = opts.get("detailing_options") or {}
    # EXTERNAL detailing: when set, this structural stage runs NO in-process
    # detailing but ALSO serializes the compiled ada.Part to a neutral IFC artifact
    # (+ a per-Beam section sidecar) at the given keys, so the chained
    # ``procedural_detail`` job on the detailing engine's capability pool can read it.
    detailing_external = bool(opts.get("detailing_external"))
    structural_ifc_key = opts.get("structural_ifc_key")
    structural_sections_key = opts.get("structural_sections_key")
    # An ephemeral *preview* build carries the current (uncommitted) document
    # inline: compile THAT instead of the DB revision's doc, and skip the
    # revision-match check (a preview isn't tied to a persisted revision). The
    # model row is still loaded for its scope + name + catalog/CAD resolution.
    preview_doc = opts.get("preview_doc")
    is_preview = isinstance(preview_doc, dict)

    # ── This compile's RUN identity ─────────────────────────────────
    # The queue job id IS the run id. It is minted per compile ATTEMPT (where the
    # derived key is content-addressed and therefore shared by every attempt of an
    # unchanged input), it is the id the compile response already hands the
    # viewer, and it is the ``audit_log`` join key — so one id names this run's log
    # blob, the panel's current run, and the admin audit entry for it.
    run_log: dict[str, str] = {"key": "", "body": ""}

    async def _write_run_log(status: str, body: str | None = None) -> None:
        """(Re)write THIS run's log with the given outcome. Called on every exit
        path, so a run always leaves a log behind — including one that fails before
        the engine is ever entered, which used to leave the panel showing whatever
        the previous run wrote."""
        if body is not None:
            run_log["body"] = body
        header = _compile_run_header(
            run_id=job_id,
            model_id=model_id,
            revision=revision,
            engine=engine,
            lod=lod,
            detailing=detailing,
            is_preview=is_preview,
            status=status,
        )
        text = f"{header}\n{run_log['body']}" if run_log["body"] else header
        run_log["key"] = await _put_run_log(storage, scope, model_id, job_id, text) or ""

    async def _fail(stage: str, msg: str, trace: str | None = None) -> None:
        await queue.update(job_id, status=JOB_STATUS_ERROR, stage=stage, error=msg)
        # Rewrite the run log with the failure banner, keeping whatever the engine
        # had already emitted — a failed run's log must stay retrievable and must
        # read as a FAILURE, distinguishable from the success that may follow it.
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

    if not model_id or not isinstance(revision, int):
        await _fail("build", "conversion_options.model_id and revision are required for procedural_build")
        return
    # Claim the artifact key for this run BEFORE any work: a lookup that has only
    # the derived key to go on (a cache hit, or a run that dies before writing
    # bytes) then resolves to this run rather than to whichever ran last.
    await _put_run_pointer(storage, scope, job.derived_key, job_id)
    if db_pool is None:
        await _fail("build", "procedural build requires DATABASE_URL on the worker")
        return

    from .. import db as db_module

    row = await db_module.get_procedural_model(db_pool, model_id)
    if row is None:
        await _fail("build", f"procedural model {model_id} not found")
        return
    if not is_preview and row["revision"] != revision:
        await _fail(
            "build",
            f"procedural model {model_id} is at revision {row['revision']}, job requested r{revision} — "
            "re-trigger compile for the current revision",
        )
        return
    # The document to compile: the inline preview doc, or the DB revision's doc.
    doc = preview_doc if is_preview else row["doc"]

    from ada.topo_model.engines import (
        BUILTIN_ENGINES,
        compile_with_engine,
        is_default_engine,
    )

    # A non-builtin engine selection is a registered (DB) engine: resolve its
    # manifest by slug to get the entrypoint. The engine's package is pre-installed
    # in this worker's capability image (that's why the job was routed here), so
    # no install happens — the entrypoint module is imported like any other.
    external_entrypoint: str | None = None
    if not is_default_engine(engine) and engine not in BUILTIN_ENGINES:
        eng_row = await db_module.get_procedural_engine_by_slug(
            db_pool, scope_kind=row["scope_kind"], scope_id=row["scope_id"], slug=engine
        )
        eng_doc = ((eng_row or {}).get("doc") if eng_row else None) or _advertised_engine_doc(engine)
        if eng_doc is None:
            await _fail("build", f"procedural engine {engine!r} is neither registered in scope nor advertised here")
            return
        external_entrypoint = eng_doc.get("entrypoint")
        if not external_entrypoint:
            await _fail("build", f"engine {engine!r} manifest has no entrypoint")
            return

    # Full-fidelity source: a model imported from an external workbook carries its
    # original file (source_xlsx_key) so a non-default engine can compile the source
    # directly (all config the topology doc drops). Fetch it for the engine.
    source_xlsx: bytes | None = None
    source_key = doc.get("source_xlsx_key")
    if source_key and not is_default_engine(engine):
        try:
            source_xlsx = await storage.get_bytes(scope, source_key)
        except Exception:
            logger.warning("procedural: source workbook %s unreadable; compiling from the doc", source_key)

    # Resolve placed catalog equipment (by slug) to its per-scope definition.
    catalog = await db_module.get_equipment_docs_by_scope(
        db_pool, scope_kind=row["scope_kind"], scope_id=row["scope_id"]
    )

    # When equipment_cad is on, prefetch the linked CAD assets for the catalog
    # slugs the model actually places, so the compiler can splice in real
    # geometry instead of boxes.
    cad_bytes: dict[str, tuple[bytes, str]] = {}
    if doc.get("equipment_cad"):
        used = {(e.get("DESCRIPTION") or "").strip() for e in (doc.get("equipments") or [])}
        cad_keys = await db_module.get_equipment_cad_keys_by_scope(
            db_pool, scope_kind=row["scope_kind"], scope_id=row["scope_id"]
        )
        for slug, cad_key in cad_keys.items():
            if slug and slug in used and cad_key:
                try:
                    data = await storage.get_bytes(scope, cad_key)
                    cad_bytes[slug] = (data, pathlib.PurePosixPath(cad_key).suffix.lower())
                except Exception:
                    logger.warning("procedural: CAD asset %s for %r unreadable; using box", cad_key, slug)

    # The quantity take-off computed alongside a DEFAULT-engine compile (the
    # structured model is in-process there); persisted as a ``.stats.json`` sibling
    # of the GLB for the viewer's Stats panel. Non-default engines don't expose an
    # ada.Part here, so their stats stay absent and the panel degrades gracefully.
    takeoff_holder: dict[str, dict] = {}
    # For an EXTERNAL-detailing build the compiled ada.Part is captured here so it
    # can be serialized to the neutral structural artifact after the GLB upload.
    assembly_holder: dict[str, object] = {}

    def _do_compile() -> bytes:
        # A non-default engine gets the raw document through the uniform
        # ``compile(doc, **options)`` contract — catalog/CAD resolution is a
        # default-engine feature (it needs the DB), so it's skipped for others.
        # Built-in slugs (echo) dispatch by slug; a registered engine dispatches
        # via its manifest entrypoint (module:callable, resolved above).
        if not is_default_engine(engine):
            selector = engine if engine in BUILTIN_ENGINES else external_entrypoint
            # source_xlsx (when the model stored its workbook) drives the engine's
            # full-fidelity path; compile_with_engine passes only the kwargs the
            # engine accepts, so a doc-only engine ignores it.
            return compile_with_engine(selector, doc, name=row["name"], lod=lod, source_xlsx=source_xlsx)
        cad_meshes = {}
        for slug, (data, ext) in cad_bytes.items():
            # Honor the type's Z-up assumption so the spliced geometry lands in
            # the same frame the bbox was inferred in (default True = verbatim).
            z_up = bool((catalog.get(slug) or {}).get("cad_z_up", True))
            try:
                cad_meshes[slug] = _load_cad_mesh(data, ext, z_up=z_up)
            except Exception:
                logger.warning("procedural: failed to load CAD mesh for %r; using box", slug)
        # The user-selected structural blueprint rides on the document
        # (``blueprint_name``, out of the whitelisted ``blueprint`` options); an
        # unset/unknown name falls back to ``steel_stru`` for backward compat.
        bp_name = doc.get("blueprint_name")
        blueprint_name = bp_name if bp_name in ("steel_stru", "none") else "steel_stru"
        # The in-process detailing engine runs as stage 2 inside the builder
        # (right where the old girder-joint pass ran, before to_glb()). Only a
        # builtin detailing slug is applied here; None/"none"/external names add
        # nothing (external = a Phase-2 chained capability job).
        from ada.topo_model.detailing_catalog import detailing_engine_specs

        builtin_detailing = {s["slug"] for s in detailing_engine_specs() if s.get("inprocess")}
        detailing_arg = detailing if detailing in builtin_detailing else None
        # An external-detailing build keeps the live ada.Part so it can be
        # serialized to the neutral structural artifact after tessellation.
        if detailing_external:
            from ada.topo_model.compile import compile_procedural_doc_with_assembly

            glb_bytes, stats, assembly = compile_procedural_doc_with_assembly(
                doc,
                name=row["name"],
                blueprint_name=blueprint_name,
                equipment_resolver=catalog.get,
                cad_scene_resolver=cad_meshes.get,
                lod=lod,
                detailing=None,
            )
            takeoff_holder["stats"] = stats
            assembly_holder["assembly"] = assembly
            return glb_bytes
        from ada.topo_model.compile import compile_procedural_doc_with_takeoff

        glb_bytes, stats = compile_procedural_doc_with_takeoff(
            doc,
            name=row["name"],
            blueprint_name=blueprint_name,
            equipment_resolver=catalog.get,
            cad_scene_resolver=cad_meshes.get,
            lod=lod,
            detailing=detailing_arg,
            detailing_options=detailing_options,
        )
        takeoff_holder["stats"] = stats
        return glb_bytes

    loop = asyncio.get_running_loop()
    # Capture the engine's logging (and stdout) DURING the compile so the messages
    # are inspectable from the viewer — persisted under THIS RUN's key
    # (procedural_run_log_key) on BOTH success and failure so errors stay
    # diagnosable and no run can ever be handed another run's output.
    stdout_buf = io.StringIO()

    def _do_compile_captured() -> bytes:
        with contextlib.redirect_stdout(stdout_buf):
            return _do_compile()

    with _capture_compile_logs() as log_handler:
        try:
            await queue.update(job_id, stage="build", progress=0.40)
            glb_bytes = await loop.run_in_executor(None, _do_compile_captured)
        except Exception as exc:
            logger.exception("worker: procedural_build failed for %s", model_id)
            await _write_run_log(
                "failed at build", _assemble_compile_log(log_handler, stdout_buf, tb_module.format_exc())
            )
            await _fail("build", str(exc), tb_module.format_exc())
            return
        # Unconditional: an engine that logged nothing still gets a blob (its banner
        # alone), so the next reader sees THIS run and not a leftover from an older one.
        await _write_run_log("ok", _assemble_compile_log(log_handler, stdout_buf, None))

    try:
        await queue.update(job_id, stage="upload", progress=0.90)
        await storage.put_bytes(scope, job.derived_key, glb_bytes, content_encoding="gzip")
    except Exception as exc:
        logger.exception("worker: procedural_build upload failed for %s", model_id)
        await _fail("upload", str(exc), tb_module.format_exc())
        return

    # Bind this artifact to the catalog state it was built from, so a later catalog
    # edit invalidates the (revision/doc-hash-stamped) cache and forces a recompile.
    await _write_catalog_fp_sidecar(storage, scope, job.derived_key, job.conversion_options)

    # Take-off stats sidecar (default-engine builds only): a gzip-at-rest
    # ``.stats.json`` sibling of the GLB (procedural_stats_key). Best-effort — a
    # failure here must not fail an otherwise-good compile; the panel degrades.
    stats = takeoff_holder.get("stats")
    if stats is not None:
        try:
            import json as _json

            from ..procedural import procedural_stats_key

            await storage.put_bytes(
                scope,
                procedural_stats_key(job.derived_key),
                _json.dumps(stats).encode("utf-8"),
                content_encoding="gzip",
            )
        except Exception:
            logger.exception("worker: procedural_build stats sidecar upload failed for %s", model_id)

    # EXTERNAL detailing: serialize the compiled ada.Part to the neutral structural
    # artifact (IFC bytes) + a per-Beam section sidecar the chained procedural_detail
    # job reads. A hard failure here (unlike the best-effort stats sidecar): the
    # external pipeline can't proceed without the artifact, so surface it.
    assembly = assembly_holder.get("assembly")
    if detailing_external and assembly is not None and structural_ifc_key and structural_sections_key:
        try:
            import json as _json

            await queue.update(job_id, stage="artifact", progress=0.95)
            ifc_bytes, sections = await loop.run_in_executor(None, _serialize_structural_artifact, assembly)
            await storage.put_bytes(scope, structural_ifc_key, ifc_bytes, content_encoding="gzip")
            await storage.put_bytes(
                scope, structural_sections_key, _json.dumps(sections).encode("utf-8"), content_encoding="gzip"
            )
        except Exception as exc:
            logger.exception("worker: procedural_build structural artifact failed for %s", model_id)
            await _fail("artifact", str(exc), tb_module.format_exc())
            return

    await _prune_run_logs(storage, scope, model_id, run_log["key"])
    await queue.update(job_id, status=JOB_STATUS_DONE, stage="ready", progress=1.0, error=None)
    # Hand the run's log to the audit row, so the admin panel's existing per-row
    # "Log" tab (GET /admin/audit/{id}/log) serves a compile exactly as it serves
    # a conversion — no parallel surface for procedural runs.
    await _audit_done(
        db_pool,
        job_id,
        "done",
        None,
        started_at,
        metrics={"log_key": run_log["key"]} if run_log["key"] else None,
    )


def _serialize_structural_artifact(assembly) -> "tuple[bytes, dict]":
    """Serialize a compiled structural ``ada.Part`` to the NEUTRAL artifact an
    external (Tier-B) detailing engine consumes: IFC bytes + a per-Beam section
    sidecar ``{member_name: {"section_type": <BOX/…>, "section_props": {...}}}``.

    The sidecar is authoritative for section-type detection (a consumer matches on
    ``section.type.value.upper()``) so a consumer never has to re-derive
    it from a potentially lossy IFC round-trip. ``section_props`` carries the
    numeric geometry (``h``/``w_top``/``t_w``/``r``/``wt``/…) present on the section."""
    import ada
    from ada.api.beams import Beam

    sections: dict[str, dict] = {}
    for bm in assembly.get_all_physical_objects(by_type=Beam):
        sec = bm.section
        props = {
            "name": sec.name,
            "h": sec.h,
            "w_top": sec.w_top,
            "w_btn": sec.w_btn,
            "t_w": sec.t_w,
            "t_ftop": sec.t_ftop,
            "t_fbtn": sec.t_fbtn,
            "r": sec.r,
            "wt": sec.wt,
        }
        sections[bm.name] = {
            "section_type": sec.type.value,
            "section_props": {k: v for k, v in props.items() if v is not None},
        }

    fd, tmp_name = tempfile.mkstemp(suffix=".ifc")
    os.close(fd)
    tmp_path = pathlib.Path(tmp_name)
    try:
        # In-memory ifcopenshell writer (no OCC): the freshly built concept objects
        # emit analytic profiles/solids straight to SPF. file_obj_only would keep it
        # in RAM but we need bytes on disk to read back uniformly.
        if not isinstance(assembly, ada.Assembly):
            assembly = ada.Assembly("StructuralArtifact") / assembly
        assembly.to_ifc(tmp_path, file_obj_only=False)
        return tmp_path.read_bytes(), sections
    finally:
        tmp_path.unlink(missing_ok=True)


class ProceduralBuildHandler(SyntheticFormatHandler if not False else SourceFormatHandler):
    kind = "procedural_build"

    async def run(self, job: Job, ctx: JobContext) -> None:
        await _run_procedural_build(
            job=job,
            scope=ctx.scope,
            storage=ctx.storage,
            queue=ctx.queue,
            db_pool=ctx.db_pool,
            started_at=ctx.started_at,
        )
