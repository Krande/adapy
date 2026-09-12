"""Exporting a loaded Assembly (GLB / IFC / Genie XML / GNX), the ``_via_ada`` entry every
ada-loadable pair shares, and bundle unpacking.
"""

from __future__ import annotations

import io
import pathlib
from typing import TYPE_CHECKING

from ada.core.file_system import new_temp_path

from .ada_load import (
    _FEM_SOURCE_EXTS,
    _GXML_TARGETS,
    _apply_fem_to_objects,
    _gxml_face_streaming,
    _load_with_ada,
    _model_has_thick_curved_shells,
    _native_ngeom_mesh_route,
)
from .keys import _FALSE, _GXML_SOURCE_EXTS, _TRUE, _ext
from .mesh_step import _seed_empty_scene
from .pipelines import (
    _STEP_GLB_PIPELINE_ADACPP_NATIVE,
    _STEP_GLB_PIPELINE_FALLBACK,
    _cad_config_for_pipeline,
    _glb_engine_stream_value,
    _native_track_for_engine,
    _resolve_step_glb_pipeline,
    _step_glb_fallback_chain,
)
from .registry import ProgressFn, UnsupportedFormat

if TYPE_CHECKING:
    pass


def _export_with_ada(
    model,
    target_format: str,
    out_path: pathlib.Path,
    on_progress: ProgressFn,
    *,
    merge_meshes: bool | None = None,
    source_ext: str | None = None,
    merge_fem_objects: bool | None = None,
    reconstruct_surfaces: bool | None = None,
    glb_tess_engine: str | None = None,
    strict_tess: bool | None = None,
) -> bytes | pathlib.Path:
    """Run the matching ada exporter; return the output as bytes or a path.

    GLB tessellates into a ``BytesIO`` and is returned as bytes. The
    disk-writing targets (IFC, Genie XML) return ``out_path`` itself —
    ownership transfers to the caller, which streams the file straight to
    object storage rather than reading it back into a RAM buffer (the
    streaming writers already keep peak memory bounded; reading the whole
    result back would undo that).

    ``merge_meshes`` is the per-job override for the ada-loadable →
    GLB pipeline. ``True`` (default) merges every geometry into one
    glTF node per material; ``False`` yields one node per source
    object (debug aid — lets you compare exporter output by Plate /
    Beam / Face name in any glTF viewer). ``None`` falls back to the
    ``ADA_GLB_MERGE_MESHES`` env var so admin-flipped defaults and
    explicit per-job kwargs both work; the kwarg wins when set.
    """
    if target_format == "glb":
        on_progress("tessellating", 0.55)
        buf = io.BytesIO()
        import os as _os

        if merge_meshes is None:
            merge_env = (_os.environ.get("ADA_GLB_MERGE_MESHES") or "").strip().lower()
            merge_meshes = merge_env not in {"0", "false", "no", "off"}
        # Watertight cap<->wall seam-weld for thick curved shells (hull strakes / gxml curved
        # plates): route their shared near-full faces through boundary-first CDT so per-solid
        # welding closes the seam that the UV-grid fast path leaves cracked. Scoped over the whole
        # GLB leg — both the native record route and the Python ``to_gltf`` fall-through read
        # ADA_TESS_WT_CDT_FULL_PATCH in adacpp. Only set when such a plate is present (the adacpp
        # side further gates on real shared-edge pins), so flat-plate models are untouched;
        # an explicit ambient value is respected. Restored in the finally for the next job.
        _prev_cdt = _os.environ.get("ADA_TESS_WT_CDT_FULL_PATCH")
        if _prev_cdt is None and _model_has_thick_curved_shells(model):
            _os.environ["ADA_TESS_WT_CDT_FULL_PATCH"] = "1"
        try:
            # Fully-native record path (Genie-XML sources): adacpp tessellates and writes the GLB
            # itself — no Python scene assembly. merge_meshes=False is the one-node-per-object debug
            # layout, which the merge-by-colour native writer can't express; that stays on Python.
            if merge_meshes:
                native_out = _native_ngeom_mesh_route(
                    model, source_ext, "glb", out_path, on_progress, glb_tess_engine=glb_tess_engine
                )
                if native_out is not None:
                    return native_out
            # FEM beam (line) elements render as line geometry by default; the solid (swept-
            # profile) representation is delivered as a separate beam_solids sidecar the viewer
            # lazy-loads when the "show beams as solid" toggle is on (mirrors the FEA-results path).
            #
            # Tessellation-engine selection (glb_tess_engine row option): to_gltf's BatchTessellator
            # reads ADA_STREAM_TESS_PIPELINE, so set it from the per-job engine for the duration of
            # the call and restore after. None/occ-builtin → force the OCC default (clear any ambient
            # override); libtess2/adacpp-* → the matching OCC-free stream pipeline.
            _stream = _glb_engine_stream_value(glb_tess_engine)
            _prev_stream = _os.environ.get("ADA_STREAM_TESS_PIPELINE")
            if _stream:
                _os.environ["ADA_STREAM_TESS_PIPELINE"] = _stream
            else:
                _os.environ.pop("ADA_STREAM_TESS_PIPELINE", None)
            # Strict coverage (only meaningful alongside a non-OCC engine): make a stream→OCC
            # fallback a hard error. Set the flag for the duration of to_gltf, restored below.
            _prev_strict = _os.environ.get("ADA_STREAM_TESS_STRICT")
            if strict_tess and _stream:
                _os.environ["ADA_STREAM_TESS_STRICT"] = "1"
            else:
                _os.environ.pop("ADA_STREAM_TESS_STRICT", None)
            try:
                model.to_gltf(buf, merge_meshes=merge_meshes)
            except ValueError as exc:
                from ada.cadit.wasm_convert import _is_empty_scene

                if not _is_empty_scene(exc):
                    raise
                # The source parsed to zero renderable geometry (e.g. a SAT file holding only a
                # wire/construction body, or an IFC with metadata only). Emit a valid seeded GLB so
                # the conversion succeeds with an empty scene instead of erroring — same trick the
                # step-stream and scene-based glb paths already use via _seed_empty_scene.
                import trimesh

                scene = trimesh.Scene()
                _seed_empty_scene(scene)
                buf = io.BytesIO()
                scene.export(buf, file_type="glb")
            finally:
                if _prev_stream is None:
                    _os.environ.pop("ADA_STREAM_TESS_PIPELINE", None)
                else:
                    _os.environ["ADA_STREAM_TESS_PIPELINE"] = _prev_stream
                if _prev_strict is None:
                    _os.environ.pop("ADA_STREAM_TESS_STRICT", None)
                else:
                    _os.environ["ADA_STREAM_TESS_STRICT"] = _prev_strict
            on_progress("ready", 1.0)
            return buf.getvalue()
        finally:
            if _prev_cdt is None:
                _os.environ.pop("ADA_TESS_WT_CDT_FULL_PATCH", None)
            else:
                _os.environ["ADA_TESS_WT_CDT_FULL_PATCH"] = _prev_cdt
    if target_format == "ifc":
        on_progress("writing-ifc", 0.55)
        # The DEFAULT xml->ifc path is the streaming writer below (model.to_ifc(streaming=True)):
        # it emits TYPED products (IfcBeam/IfcPlate with parametric round-trip) while curved and
        # spline-boundary plates stream their heavy B-rep body graphs through adacpp's
        # ngeom_to_ifc_body_spf C++ fragment emitter (~µs/face vs the per-entity ifcopenshell
        # writer's ~ms/face that made the thickened-curved-shell hull take ~34 s).
        # ADA_CAD_NATIVE_NGEOM_EXPORT_IFC=true remains a GEOMETRY-ONLY opt-in: the fully-native
        # record-stream writer wraps every solid in an IfcBuildingElementProxy (no typed
        # products), acceptable only for geometry handoff. The STEP leg stays native by default:
        # STEP products carry name-only semantics either way, so nothing is lost there.
        if source_ext is not None and source_ext.lower() in _GXML_SOURCE_EXTS:
            import os as _os

            from ada.cadit.ngeom.export import (
                NativeExportUnsupported,
                native_export_enabled,
                native_ngeom_writers_available,
                native_to_ifc,
            )

            _ifc_opt_in = _os.environ.get("ADA_CAD_NATIVE_NGEOM_EXPORT_IFC", "").strip().lower() in ("1", "true")
            if _ifc_opt_in and native_export_enabled() and native_ngeom_writers_available():
                try:
                    native_to_ifc(model, out_path)
                    on_progress("ready", 1.0)
                    return out_path
                except NativeExportUnsupported as exc:
                    from ada.config import logger

                    logger.warning("native xml->ifc route unavailable (%s); using the Python writer", exc)
        # Memory-bounded writer is the default: it hand-authors Plate solids as
        # SPF text instead of holding the whole ifcopenshell.file, ~halving peak
        # RSS on large FEM→IFC and clearing the worker OOM cap. The admin "Stream
        # IFC write" toggle / per-job ``ifc_streaming`` sets ADA_IFC_STREAMING;
        # only an explicit falsy value reverts to the in-memory writer.
        import os

        streaming = os.environ.get("ADA_IFC_STREAMING", "").strip().lower() not in _FALSE
        # On the streaming path, fold FEM shells via the shared object-free face engine
        # instead of emitting one plate per element (matches the Genie-XML and STEP
        # streamers). Default is analytic auto-detect ("cylinder": tubular members ->
        # IfcCylindricalSurface, flat panels -> merged IfcPlane faces), same as FEM->STEP;
        # a string merge_fem_objects overrides verbatim, False opts out to 1:1.
        ms = None
        recon = bool(reconstruct_surfaces) if reconstruct_surfaces is not None else False
        if streaming and source_ext is not None and source_ext.lower() in _FEM_SOURCE_EXTS and not recon:
            if isinstance(merge_fem_objects, str):
                ms = merge_fem_objects
            elif merge_fem_objects is False:
                ms = "none"
            else:
                ms = "cylinder"  # analytic auto-detect
        model.to_ifc(destination=str(out_path), streaming=streaming, merge_strategy=ms)
    elif target_format in _GXML_TARGETS:
        on_progress("writing-gnx" if target_format == "gnx" else "writing-xml", 0.55)
        recon = bool(reconstruct_surfaces) if reconstruct_surfaces is not None else False
        # gnx = the same concept XML zipped into a Genie workspace with its ACIS
        # body beside it; both routes below take the same writer choice.
        genie_write = model.to_gnx if target_format == "gnx" else model.to_genie_xml
        if source_ext is not None and _gxml_face_streaming(source_ext, target_format, recon):
            # Object-free path: plates stream from the vectorized FEM-shell face
            # source (no Plate objects, no DOM). Default is the analytic auto-detect
            # ("cylinder": tubular members -> <curved_shell> over an embedded SAT
            # body, flat panels -> merged <flat_plate>), matching FEM->STEP/IFC and
            # collapsing a tube's shell facets instead of emitting thousands of
            # coplanar polygons; a string merge_fem_objects overrides verbatim,
            # False opts out to 1:1.
            if isinstance(merge_fem_objects, str):
                ms = merge_fem_objects
            elif merge_fem_objects is False:
                ms = "none"
            else:
                ms = "cylinder"
            genie_write(str(out_path), streaming=True, merge_strategy=ms)
        else:
            genie_write(str(out_path))
    else:
        raise UnsupportedFormat(f"unknown target format: {target_format!r}")
    on_progress("ready", 1.0)
    return out_path


# A STEP file on disk above this size loads into one OCC compound that OOM-kills
# the worker (the 778 MB CAD assembly is fatal). Above it, STEP→GLB auto-routes
# through the memory-bounded streaming converter; below, the OCC path keeps full
# fidelity. Admin-tunable via the conversion settings (env rail below).
_STEP_STREAM_DEFAULT_THRESHOLD_MB = 200.0


def _should_stream_step(src_path: pathlib.Path, step_streamer: bool | None) -> bool:
    """Decide whether STEP→GLB goes through the streaming converter.

    Precedence: explicit per-job choice (``step_streamer`` kwarg or the
    ``ADA_STEP_STREAMER`` env the worker sets from the job option) wins; otherwise
    auto-select by file size, gated by the global ``ADA_STEP_STREAMER_AUTO`` toggle
    and ``ADA_STEP_STREAMER_THRESHOLD_MB`` (both admin settings)."""
    import os

    if step_streamer is None:
        raw = os.environ.get("ADA_STEP_STREAMER", "").strip().lower()
        if raw in _TRUE:
            return True
        if raw in _FALSE:
            return False
    else:
        return bool(step_streamer)

    if os.environ.get("ADA_STEP_STREAMER_AUTO", "").strip().lower() in _FALSE:
        return False  # auto-streaming disabled globally
    try:
        threshold_mb = float(os.environ.get("ADA_STEP_STREAMER_THRESHOLD_MB", "") or _STEP_STREAM_DEFAULT_THRESHOLD_MB)
    except ValueError:
        threshold_mb = _STEP_STREAM_DEFAULT_THRESHOLD_MB
    try:
        return src_path.stat().st_size > threshold_mb * 1024 * 1024
    except OSError:
        return False


def _via_ada(
    src_path: pathlib.Path,
    source_ext: str,
    target_format: str,
    on_progress: ProgressFn,
    *,
    merge_meshes: bool | None = None,
    fem_to_objects: bool | None = None,
    merge_fem_objects: bool | None = None,
    reconstruct_surfaces: bool | None = None,
    step_streamer: bool | None = None,
    step_glb_pipeline: str | None = None,
    glb_tess_engine: str | None = None,
    strict_tess: bool | None = None,
    occ_clickable: bool = False,
) -> bytes:
    """Heavy path: load with ada, export to target format. Used for any
    non-trivial source/target combination that needs the full ada-py
    stack. Source already lives on disk (worker streamed it there).

    ``merge_meshes`` is forwarded to :func:`_export_with_ada` so the
    per-job kwarg path established by the convert() options dispatch
    reaches the actual GLB writer call. Other targets ignore it.

    ``step_streamer`` (STEP→GLB only) forces the memory-bounded streaming
    converter; ``None`` auto-selects it for STEP files too large for the OCC
    loader to hold without OOM-killing the worker.

    ``occ_clickable`` (STEP→GLB only) routes to the OCC per-face clickable path
    (``convert_step_to_occ_clickable_glb``): OCC-tessellated geometry with
    ``face_ranges_node`` keyed by STEP entity id. Set by the dispatch when the
    ``python`` serializer is chosen with the ``face_regions`` toggle on.
    """
    suffix = ".glb" if target_format == "glb" else f".{target_format}"
    out_path = new_temp_path(suffix=suffix)
    result: bytes | pathlib.Path = b""
    try:
        if source_ext in {".step", ".stp"} and target_format == "glb":
            if occ_clickable:
                # OCC per-face clickable track (the python serializer's face_regions path).
                # OCC-tessellates every face and writes face_ranges_node itself, keyed by the
                # STEP #NNNN entity id — the same id namespace the native path stamps.
                from ada.cadit.step.occ_faces_to_glb import (
                    convert_step_to_occ_clickable_glb,
                )

                on_progress("occ-clickable-faces", 0.1)
                convert_step_to_occ_clickable_glb(src_path, out_path, linear_deflection=0.0)
                on_progress("ready", 1.0)
                result = out_path
                return result

            pipe = _resolve_step_glb_pipeline(step_glb_pipeline)

            if pipe == _STEP_GLB_PIPELINE_ADACPP_NATIVE:
                # Fully-native in-process path (C++ reader + thread pool + GLB writer). Falls through
                # to the standard pipelines below if adacpp's native entry point isn't available, so a
                # native request degrades gracefully rather than failing the job.
                from ada.cadit.step.native_step_to_glb import (
                    native_adacpp_available,
                    native_step_to_glb,
                )
                from ada.config import logger

                if native_adacpp_available():
                    try:
                        native_step_to_glb(
                            src_path,
                            out_path,
                            on_progress=on_progress,
                            pipeline=_native_track_for_engine(glb_tess_engine),
                        )
                        result = out_path
                        return result
                    except Exception as exc:
                        if bool(strict_tess):
                            raise
                        logger.warning(
                            "adacpp-native STEP->GLB failed for %s (%s); falling back to %s",
                            getattr(src_path, "name", src_path),
                            exc,
                            _STEP_GLB_PIPELINE_FALLBACK,
                        )
                else:
                    logger.warning(
                        "adacpp-native requested but unavailable; falling back to %s", _STEP_GLB_PIPELINE_FALLBACK
                    )
                pipe = _STEP_GLB_PIPELINE_FALLBACK

            cad_cfg = _cad_config_for_pipeline(pipe)
            if cad_cfg is not None:
                # Default path: adacpp tessellation (libtess2 / occ / cgal / hybrid) through the
                # memory-bounded streaming pipeline + its worker pool. libtess2 carries the curved
                # surfaces the OCC stream reader drops, at step2glb-parity geometry.
                from ada.cadit.step.stream_to_glb import stream_step_to_glb
                from ada.config import logger
                from ada.occ.tessellating import TessellationFallbackError

                # No geometry left behind: try the requested adacpp pipeline, then adacpp's own
                # OCC kernel (adacpp:occ) — staying in the adacpp ecosystem so this also works on
                # wasm, where pythonocc isn't available. Only if every adacpp attempt yields nothing
                # do we fall through to the pythonocc occ-builtin path below (native only).
                #
                # Strict coverage (strict_tess): enforce 100% on the requested non-OCC engine —
                # drop the adacpp:occ / occ-builtin fallbacks, and fail if the run skipped any solid
                # (stream_step_to_glb skips rather than OCC-falls-back per geom), instead of shipping
                # a partial GLB or completing on OCC.
                strict = bool(strict_tess)
                chain = _step_glb_fallback_chain(pipe, cad_cfg)
                if strict:
                    chain = chain[:1]
                for fb_pipe, fb_cfg in chain:
                    try:
                        on_progress(fb_pipe, 0.1)
                        stats = stream_step_to_glb(
                            src_path, out_path, tolerant=True, on_progress=on_progress, cad_config=fb_cfg
                        )
                        if strict and stats and stats.get("skipped"):
                            raise TessellationFallbackError(
                                f"strict tessellation: {fb_pipe} skipped {stats['skipped']}/"
                                f"{stats.get('total', '?')} solids ({stats.get('reasons')})"
                            )
                        on_progress("ready", 1.0)
                        result = out_path
                        return result
                    except Exception as exc:
                        if strict:
                            raise
                        logger.warning(
                            "step-glb %s produced no usable GLB for %s (%s); trying next fallback",
                            fb_pipe,
                            getattr(src_path, "name", src_path),
                            exc,
                        )

            if _should_stream_step(src_path, step_streamer):
                # occ-builtin, large file: stream solid-by-solid (bounded memory, no whole-model
                # OCC load) via pythonocc BRepMesh. Small files fall through to the full OCC load.
                from ada.cadit.step.stream_to_glb import stream_step_to_glb

                on_progress("streaming-step", 0.1)
                stream_step_to_glb(src_path, out_path, tolerant=True, on_progress=on_progress)
                on_progress("ready", 1.0)
                result = out_path
                return result

        on_progress("parsing", 0.15)
        model = _load_with_ada(src_path, source_ext)
        _apply_fem_to_objects(model, source_ext, target_format, fem_to_objects, merge_fem_objects, reconstruct_surfaces)
        result = _export_with_ada(
            model,
            target_format,
            out_path,
            on_progress,
            merge_meshes=merge_meshes,
            source_ext=source_ext,
            merge_fem_objects=merge_fem_objects,
            reconstruct_surfaces=reconstruct_surfaces,
            glb_tess_engine=glb_tess_engine,
            strict_tess=strict_tess,
        )
        return result
    finally:
        # When we hand back the path itself, ownership transfers to the
        # caller (the subprocess child moves it into the result slot), so we
        # must NOT delete it here. Bytes results — and the empty temp file
        # GLB never writes (it tessellates into a BytesIO) — get cleaned up.
        if not isinstance(result, pathlib.Path):
            try:
                out_path.unlink()
            except OSError:
                pass


def _via_bundle(
    src_path: pathlib.Path,
    target_format: str,
    on_progress: ProgressFn,
    *,
    options: dict | None = None,
) -> bytes:
    """Unpack a zip, validate the include chain, then run ada-py on the
    entry-point with the bundle's tempdir as cwd so relative INCLUDEs
    resolve.

    Bundle errors propagate as :class:`bundle.BundleError`, which the
    worker translates into a job-level ``error`` audit row with the
    user-visible reason ("missing include: foo.inp", "ambiguous
    entry-point: a.inp, b.inp", etc.).

    ``options`` is forwarded to :func:`_export_with_ada` so per-job
    knobs (``merge_meshes``, …) survive the unpack indirection.
    """
    from .. import bundle as bundle_mod

    on_progress("unpacking", 0.05)
    # The bundle module currently inspects from a bytes blob; reading
    # the zip from disk is fine — bundles are bounded (validation
    # rejects pathological archives before we'd OOM).
    data = src_path.read_bytes()
    tmp, info = bundle_mod.unpack_and_inspect(data)
    opts = options or {}
    try:
        on_progress("parsing", 0.20)
        # ada.from_fem reads the file at `info.entry`; the includes it
        # references are next to it under the same tempdir, so relative
        # resolution Just Works without us touching cwd.
        entry_ext = _ext(info.entry.name)
        model = _load_with_ada(info.entry, entry_ext)
        _apply_fem_to_objects(
            model,
            entry_ext,
            target_format,
            opts.get("fem_to_objects"),
            opts.get("merge_fem_objects"),
            opts.get("reconstruct_surfaces"),
        )
        suffix = ".glb" if target_format == "glb" else f".{target_format}"
        out_path = new_temp_path(suffix=suffix)
        result: bytes | pathlib.Path = b""
        try:
            result = _export_with_ada(
                model,
                target_format,
                out_path,
                on_progress,
                merge_meshes=opts.get("merge_meshes"),
                source_ext=entry_ext,
                merge_fem_objects=opts.get("merge_fem_objects"),
                reconstruct_surfaces=opts.get("reconstruct_surfaces"),
                glb_tess_engine=opts.get("glb_tess_engine"),
                strict_tess=opts.get("strict_tess"),
            )
            return result
        finally:
            # Path result → ownership transfers to the caller; only clean up
            # when the bytes are already in hand (see _via_ada).
            if not isinstance(result, pathlib.Path):
                try:
                    out_path.unlink()
                except OSError:
                    pass
    finally:
        tmp.cleanup()
