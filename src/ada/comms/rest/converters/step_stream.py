"""STEP / IFC exports that bypass the whole-model OCC Assembly: per-solid native NGEOM streams
into the AP242 / IFC4 / Genie XML writers and the native IFC → GLB pipeline.
"""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

from ada.core.file_system import new_temp_path

from .ada_export import _via_ada
from .mesh_step import _via_ada_to_step
from .pipelines import _native_track_for_engine
from .registry import ConverterRegistry, ProgressFn
from .serializers import (
    _apply_glb_serializer,
    _brep_writer_is_python,
    _conversion_path_options,
)

if TYPE_CHECKING:
    pass


def _via_step_stream_to_step(
    src_path: pathlib.Path,
    on_progress: ProgressFn,
) -> pathlib.Path:
    """STEP → STEP (AP242) via the per-solid NGEOM stream — **no OCC**.

    The native adacpp NGEOM reader (pure-Python stream reader as a drop-in
    fallback) yields one analytic ``ada.geom.Geometry`` per solid — full B-rep
    incl. B-spline surfaces/curves and swept surfaces, plus colour, world
    placement and name — and each is hand-authored straight to STEP Part-21 by
    the kernel-free :class:`Ap242StreamWriter`. Peak memory is O(one solid), so
    the multi-GB assemblies that OOM/timed out through ``ada.from_step`` →
    ``to_stp`` now stream through. No tessellation, no OCC anywhere in the path.
    """
    from ada.config import logger

    out_path = new_temp_path(suffix=".step")
    from ada.cadit.step.native_step_to_step import (
        native_step_to_step,
        native_step_to_step_available,
    )

    if native_step_to_step_available():
        try:
            stats = native_step_to_step(src_path, out_path, on_progress=on_progress)
            logger.info("native STEP->STEP: %s", stats)
            return out_path
        except Exception as exc:  # noqa: BLE001 - degrade to the Python AP242 writer
            logger.warning("native STEP->STEP failed (%s); falling back to per-solid Python", exc)

    from ada.cadit.step.write.stream_step_to_step import stream_step_to_step

    stats = stream_step_to_step(src_path, out_path, on_progress=on_progress)
    logger.info("stream STEP->STEP (python): %s", stats)
    return out_path


def _via_ifc_to_step(
    src_path: pathlib.Path,
    on_progress: ProgressFn,
) -> pathlib.Path:
    """IFC → AP242 STEP via the native adacpp IFC B-rep reader → ng:: → STEP writer — **no OCC**.

    A native IFC advanced-B-rep reader (analytic surfaces/curves + IfcMappedItem instancing) builds
    ng:: neutral geometry which the AP242 STEP emitter re-writes (instances baked). The declared length
    unit is preserved. Falls back to the OCC path (``_via_ada_to_step``) when the native reader can't
    fully cover the file — IFC with IfcExtrudedAreaSolid / CSG / tessellated geometry, or any
    product/face left behind — so no geometry is silently lost.
    """
    from ada.cadit.step.native_ifc_to_step import (
        native_ifc_to_step,
        native_ifc_to_step_available,
    )
    from ada.config import logger

    if native_ifc_to_step_available():
        try:
            out_path = new_temp_path(suffix=".step")
            stats = native_ifc_to_step(src_path, out_path, on_progress=on_progress)
            logger.info("native IFC->STEP: %s", stats)
            return out_path
        except Exception as exc:  # noqa: BLE001 - native can't cover this IFC; use the OCC writer
            logger.info("native IFC->STEP unavailable/incomplete (%s); using OCC", exc)
    return _via_ada_to_step(src_path, ".ifc", on_progress)


def _via_step_stream_to_ifc(
    src_path: pathlib.Path,
    on_progress: ProgressFn,
) -> pathlib.Path:
    """STEP → IFC4X3_ADD2 advanced B-rep — **no OCC, no ada Assembly**, bounded memory.

    Prefers the fully-native adacpp writer (``stream_step_to_ifc``): the same C++ reader the GLB/mesh
    paths use resolves each solid's analytic B-rep, emits it as an IfcAdvancedBrep (cones →
    IfcSurfaceOfRevolution, splines → IfcBSplineSurface, …) and places instances via IfcMappedItem,
    parallel across the cgroup-aware thread allotment. Lossless (every solid/face/edge analytic) and
    ifcopenshell-valid. Falls back to the per-solid Python writer (``stream_step_to_ifc``: hand-authors
    one ``ada.geom.Geometry`` per solid) when adacpp's native IFC entry point is absent. Either way the
    ifcopenshell.file only ever holds the spatial preamble, so the multi-GB assemblies that OOM/timed
    out through ``ada.from_step`` → ``to_ifc`` now stream through.
    """
    from ada.config import logger

    out_path = new_temp_path(suffix=".ifc")
    from ada.cadit.step.native_step_to_ifc import (
        native_ifc_available,
        native_step_to_ifc,
    )

    if native_ifc_available():
        try:
            stats = native_step_to_ifc(src_path, out_path, on_progress=on_progress)
            logger.info("native STEP->IFC: %s", stats)
            return out_path
        except Exception as exc:  # noqa: BLE001 - degrade to the Python writer
            logger.warning("native STEP->IFC failed (%s); falling back to per-solid Python", exc)

    from ada.cadit.step.write.stream_step_to_ifc import stream_step_to_ifc

    stats = stream_step_to_ifc(src_path, out_path, on_progress=on_progress)
    logger.info("stream STEP->IFC (python): %s", stats)
    return out_path


def _via_step_stream_to_xml(
    src_path: pathlib.Path,
    on_progress: ProgressFn,
) -> pathlib.Path:
    """STEP → Genie XML via the per-solid stream — **no OCC, no whole-model load**.

    Raw CAD B-rep has no Genie-XML concept representation, so the XML is the empty
    structural scaffold. We stream-parse the STEP to validate it reads (bounded,
    native C++ parser, no ada.geom hydrate) rather than the full ``ada.from_step``
    load that timed out the multi-GB assemblies for an empty output.
    """
    from ada.cadit.step.write.stream_step_to_xml import stream_step_to_xml
    from ada.config import logger

    out_path = new_temp_path(suffix=".xml")
    stats = stream_step_to_xml(src_path, out_path, on_progress=on_progress)
    logger.info("stream STEP->XML: %s", stats)
    return out_path


def _via_step_stream_to_mesh(
    src_path: pathlib.Path,
    source_ext: str,
    target_ext: str,
    on_progress: ProgressFn,
    *,
    step_glb_pipeline: str | None = None,
) -> pathlib.Path:
    """STEP → mesh container (``.obj`` / ``.stl``) **without building an ada
    Assembly**, bounded memory, no OCC.

    Prefers the fully-native adacpp pipeline (``stream_step_to_mesh``): the same C++
    reader + parallel libtess2 as the native GLB path, baking each placement and
    streaming triangles straight to disk — ~2.5x faster than per-solid Python on
    giant-solid / FEM-export STEP. Falls back to the per-solid Python writer
    (``stream_step_to_mesh``: tessellate one solid at a time via the active backend,
    transform per triangle-batch) when adacpp's native mesh entry point is absent.
    Either way peak memory is O(one solid's mesh), never a whole-model buffer.
    ``step_glb_pipeline`` is accepted for signature compatibility but unused.
    """
    out_path = new_temp_path(suffix=target_ext)
    fmt = target_ext.lstrip(".")
    from ada.cadit.step.native_step_to_mesh import (
        native_mesh_available,
        native_step_to_mesh,
    )
    from ada.config import logger

    if native_mesh_available():
        try:
            native_step_to_mesh(src_path, out_path, fmt, on_progress=on_progress)
            return out_path
        except Exception as exc:  # noqa: BLE001 - degrade to the Python writer
            logger.warning("native STEP->%s failed (%s); falling back to per-solid Python", fmt, exc)

    from ada.cadit.step.write.stream_step_to_mesh import stream_step_to_mesh

    stream_step_to_mesh(src_path, out_path, fmt, on_progress=on_progress)
    return out_path


def _via_ifc_stream_to_glb(
    src_path: pathlib.Path,
    on_progress: ProgressFn,
    *,
    glb_tess_engine: str | None = None,
    strict_tess: bool | None = None,
    force_python: bool = False,
) -> bytes | pathlib.Path:
    """IFC → GLB, fully native (adacpp ``stream_ifc_to_glb``: pure-C++ IfcResolver → libtess2 →
    merge-by-colour GLB, no ifcopenshell/OCC), with a graceful fallback to the ifcopenshell
    ``from_ifc`` → GLB path. The native GLB carries geometry + per-mesh colour + the spatial tree +
    names — viewer-equivalent (IFC property sets never live in the GLB; they are fetched on selection).
    Falls back when adacpp's native entry is absent, the run raises, or it produces 0 products (e.g. an
    IFC of only tessellated face-sets the analytic resolver skips).

    ``force_python`` (serializer='python') bypasses the pure-native path entirely so the chosen
    ``glb_tess_engine`` kernel actually drives the ifcopenshell import + BatchTessellator."""
    from ada.cadit.ifc.native_ifc_to_glb import (
        native_ifc_glb_available,
        native_ifc_to_glb,
    )
    from ada.config import logger

    if not force_python and native_ifc_glb_available():
        try:
            out_path = new_temp_path(suffix=".glb")
            # Same track plumbing as the STEP native path: the cpp serializer parks its chosen
            # track on the tess-engine axis, and this reverses it. Without forwarding it, picking a
            # track for an IFC source ran the default and reported the caller's choice back.
            stats = native_ifc_to_glb(
                src_path,
                out_path,
                on_progress=on_progress,
                pipeline=_native_track_for_engine(glb_tess_engine),
            )
            if stats.get("solids", 0) > 0:
                logger.info("native IFC->GLB: %s", stats)
                return out_path
            logger.info("native IFC->GLB produced 0 products; falling back to from_ifc")
        except Exception as exc:  # noqa: BLE001
            logger.info("native IFC->GLB failed (%s); falling back to from_ifc", exc)
    return _via_ada(src_path, ".ifc", "glb", on_progress, glb_tess_engine=glb_tess_engine, strict_tess=strict_tess)


def _register_step_stream_exports() -> None:
    # STEP/STP exports that bypass the full-OCC Assembly (which OOM-kills / times
    # out on multi-GB CAD assemblies). Registered AFTER _register_ada_loadable so
    # these OVERRIDE the generic OCC registrations for STEP sources only; all other
    # ada-loadable sources keep their OCC paths.
    #
    #  • obj/stl → memory-bounded native streaming GLB, then trimesh transcode.
    #  • step    → per-solid native NGEOM stream straight into the AP242 writer
    #              (analytic B-rep incl. B-splines; no OCC, no tessellation).
    #  • ifc     → per-solid native NGEOM stream straight into the IFC4
    #              advanced-B-rep writer (analytic; no OCC, no tessellation).
    for ext in (".step", ".stp"):
        for tgt in ("stl", "obj"):

            def _h(src, on_progress, *, _ext=ext, _tgt=tgt, step_glb_pipeline=None, **_kw):
                return _via_step_stream_to_mesh(src, _ext, f".{_tgt}", on_progress, step_glb_pipeline=step_glb_pipeline)

            ConverterRegistry.register(ext, tgt, _h)

        def _h_step(src, on_progress, *, _ext=ext, **_kw):
            return _via_step_stream_to_step(src, on_progress)

        ConverterRegistry.register(ext, "step", _h_step)

        def _h_ifc(src, on_progress, *, _ext=ext, serializer=None, tessellator=None, **_kw):
            # serializer=python routes to the OCC/ifcopenshell writer; cpp (default) uses the native
            # streaming adacpp B-rep emitter. wasm serializers run client-side (never reach here).
            if _brep_writer_is_python(serializer):
                return _via_ada(src, _ext, "ifc", on_progress)
            return _via_step_stream_to_ifc(src, on_progress)

        ConverterRegistry.register(ext, "ifc", _h_ifc, options=_conversion_path_options(ext, "ifc"))

        def _h_xml(src, on_progress, *, _ext=ext, **_kw):
            return _via_step_stream_to_xml(src, on_progress)

        ConverterRegistry.register(ext, "xml", _h_xml)

        def _h_gnx(src, on_progress, *, _ext=ext, **_kw):
            # Same streamed scaffold, repacked as a workspace.
            from ada.cadit.gxml.write.write_gnx import gnx_from_genie_xml

            xml_path = _via_step_stream_to_xml(src, on_progress)
            return gnx_from_genie_xml(xml_path, new_temp_path(suffix=".gnx"))

        ConverterRegistry.register(ext, "gnx", _h_gnx)

    # IFC → STEP via the native adacpp IFC B-rep reader → ng:: → AP242 writer (no OCC). Overrides the
    # generic OCC ifc→step ONLY when the native verb is present, so older builds keep the OCC path.
    try:
        from ada.cadit.step.native_ifc_to_step import native_ifc_to_step_available

        _native_ifc2step = native_ifc_to_step_available()
    except Exception:  # noqa: BLE001
        _native_ifc2step = False
    if _native_ifc2step:

        def _h_ifc2step(src, on_progress, *, serializer=None, tessellator=None, **_kw):
            # serializer=python routes to the OCC ifc→step writer; cpp (default) uses the native
            # adacpp B-rep reader → AP242 writer. wasm serializers run client-side.
            if _brep_writer_is_python(serializer):
                return _via_ada(src, ".ifc", "step", on_progress)
            return _via_ifc_to_step(src, on_progress)

        ConverterRegistry.register(".ifc", "step", _h_ifc2step, options=_conversion_path_options(".ifc", "step"))

    # IFC → GLB via the native adacpp IFC pipeline (no ifcopenshell/OCC), overriding the generic
    # from_ifc → GLB. Gated on the native verb; degrades to from_ifc per _via_ifc_stream_to_glb's
    # own fallback (absent verb / error / 0 products), so this is always safe to register.
    try:
        from ada.cadit.ifc.native_ifc_to_glb import native_ifc_glb_available

        _native_ifc2glb = native_ifc_glb_available()
    except Exception:  # noqa: BLE001
        _native_ifc2glb = False
    if _native_ifc2glb:

        def _h_ifc2glb(
            src, on_progress, *, glb_tess_engine=None, strict_tess=None, serializer=None, tessellator=None, **_kw
        ):
            _step_pipe, glb_tess_engine, force_python = _apply_glb_serializer(
                ".ifc",
                serializer,
                tessellator,
                step_glb_pipeline=None,
                glb_tess_engine=glb_tess_engine,
            )
            return _via_ifc_stream_to_glb(
                src,
                on_progress,
                glb_tess_engine=glb_tess_engine,
                strict_tess=strict_tess,
                force_python=force_python,
            )

        # Preserve the serializer/tessellator + engine options declared on the generic ifc→glb row.
        ConverterRegistry.register(".ifc", "glb", _h_ifc2glb, options=ConverterRegistry.options_for(".ifc", "glb"))
