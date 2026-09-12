"""Registration of the passthrough / trimesh / ada-loadable pairs, with the per-pair option
schemas the SPA renders.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.core.file_system import new_temp_path

from .ada_export import _via_ada
from .ada_load import (
    _FEM_SOURCE_EXTS,
    _GXML_TARGETS,
    _passthrough,
    _via_gltf_to_glb,
    _via_trimesh,
)
from .keys import _ADA_LOADABLE_EXTS, _TRIMESH_EXTS
from .mesh_step import _via_ada_to_step, _via_ada_to_trimesh
from .pipelines import (
    _GLB_TESS_ENGINE_DEFAULT,
    _STEP_GLB_PIPELINE_DEFAULT,
    _glb_tess_engines,
    _step_glb_pipelines,
)
from .registry import ConverterRegistry
from .serializers import (
    _GLB_SERIALIZER_PYTHON,
    _apply_glb_serializer,
    _glb_serializer_options,
)

if TYPE_CHECKING:
    pass


def _register_passthrough_glb() -> None:
    def _h(src, on_progress, **_):
        return _passthrough(src, on_progress)

    ConverterRegistry.register(".glb", "glb", _h)


def _register_trimesh_to_glb() -> None:
    def _gltf(src, on_progress, **_):
        return _via_gltf_to_glb(src, on_progress)

    ConverterRegistry.register(".gltf", "glb", _gltf)

    for ext in _TRIMESH_EXTS:
        # Closure-over-loop-var trap: bind ext via default arg so each
        # registered handler captures its own source extension and the
        # last iteration's value doesn't leak into earlier entries.
        def _h(src, on_progress, *, _ext=ext, **_kw):
            return _via_trimesh(src, _ext, on_progress)

        ConverterRegistry.register(ext, "glb", _h)


def _register_ada_loadable() -> None:
    # Schema for the ada-loadable → GLB pairs. ``merge_meshes`` flows
    # straight into :func:`_export_with_ada`'s ``model.to_gltf`` call.
    # Declared per-target so it shows up on the GLB row only — IFC /
    # XML / STL / OBJ writers don't honour it.
    glb_options = [
        {
            "name": "merge_meshes",
            "type": "bool",
            "default": True,
            "description": (
                "Merge every geometry into a single glTF node per "
                "material (default). Disable to emit one node per "
                "source object — useful for debugging tessellation "
                "by Plate / Beam / Face name."
            ),
        },
        {
            "name": "tess_linear_deflection",
            "type": "number",
            "default": 0.0,
            "description": (
                "Curved-surface tessellation quality. 0 (default) uses the lean relative "
                "mesher — smallest GLB, mobile-friendly. A positive value (in model "
                "units, e.g. mm) switches to an explicit chordal deflection: smaller = "
                "smoother curves but more triangles / larger GLB (step2glb uses ~1 mm)."
            ),
        },
        {
            "name": "tess_angular_deg",
            "type": "number",
            "default": 20.0,
            "description": (
                "Angular deflection (degrees) for the explicit-deflection mesher (only "
                "when tess_linear_deflection > 0). Drives facet count on doubly-curved "
                "surfaces (spheres / tori / B-splines); smaller = smoother."
            ),
        },
        {
            "name": "tess_relative",
            "type": "bool",
            "default": False,
            "description": (
                "Treat tess_linear_deflection as a fraction of each shape's bbox instead "
                "of absolute model units (only when tess_linear_deflection > 0)."
            ),
        },
        {
            "name": "glb_compression",
            "type": "enum",
            "default": "meshopt",
            "enum": ["off", "meshopt"],
            "description": (
                "'meshopt' applies EXT_meshopt_compression to the GLB buffers "
                "(~2.5-3x smaller download, decoded client-side). Structure-preserving: "
                "re-encodes only the vertex/index bytes losslessly and leaves the glTF JSON "
                "byte-identical, so node names, draw_ranges, id_hierarchy and ADA_EXT_data "
                "are kept and picking/hierarchy still work. On by default (gzip-at-rest applies "
                "on top); a safe no-op if meshoptimizer/adacpp isn't installed in the worker."
            ),
        },
    ]

    # STEP→GLB only: route the conversion through the memory-bounded streaming
    # reader (one solid at a time) instead of loading the whole model via OCC.
    step_streamer_option = {
        "name": "step_streamer",
        "type": "bool",
        "default": False,
        "description": (
            "Load STEP with the memory-bounded streaming reader (one solid at a "
            "time) instead of OpenCASCADE. Use for very large assemblies that "
            "OOM the normal path; skips solids using unsupported (spherical / "
            "rational B-spline) surfaces. Auto-enabled above 200 MB."
        ),
    }

    # STEP→GLB only: choose the tessellation engine. ``libtess2`` (default) is
    # adacpp's OCC-free boundary tessellator with step2glb-parity geometry incl.
    # the curved surfaces the OCC stream reader drops; ``occ-builtin`` is the prior
    # OpenCASCADE path; ``adacpp-{occ,cgal,hybrid}`` use adacpp's taxonomy kernels.
    step_glb_pipeline_option = {
        "name": "step_glb_pipeline",
        "type": "enum",
        "default": _STEP_GLB_PIPELINE_DEFAULT,
        "enum": list(_step_glb_pipelines()),
        "description": (
            "STEP→GLB tessellation engine. 'adacpp-native' (default) runs the whole STEP→GLB in "
            "adacpp C++ (reader + thread pool + GLB writer) — fastest + lowest-memory, and 1:1 with "
            "the Python path (geometry, product names, per-instance picking, full assembly tree); "
            "falls back to 'libtess2' then 'occ-builtin'. 'libtess2' is adacpp's OCC-free boundary "
            "tessellator (Python reader + worker pool) and renders the curved surfaces (rational "
            "B-spline / spherical / conical / toroidal) the OCC streaming reader silently drops. "
            "'occ-builtin' is the OpenCASCADE path. 'adacpp-occ' / 'adacpp-cgal' / 'adacpp-hybrid' "
            "use adacpp's taxonomy kernels."
        ),
    }

    # Non-STEP →GLB tessellation engine (xml / ifc / sat / fem / obj / stl → glb). Same engines
    # as STEP but driving to_gltf's BatchTessellator stream selector; default libtess2 (adacpp-aware,
    # matching the runtime `_default_glb_tess_engine`), degrading to OCC on an adacpp-less pool.
    glb_tess_engine_option = {
        "name": "glb_tess_engine",
        "type": "enum",
        "default": _GLB_TESS_ENGINE_DEFAULT,
        "enum": list(_glb_tess_engines()),
        "description": (
            "→GLB tessellation engine. 'libtess2' (default) is adacpp's OCC-free boundary "
            "tessellator — renders curved surfaces OCC "
            "drops and avoids the OCC optimal-bbox cost on curved-heavy models (per-geom fallback "
            "to OCC when a geometry isn't yet NGEOM-serializable). 'adacpp-occ' / 'adacpp-cgal' / "
            "'adacpp-hybrid' use adacpp's taxonomy kernels. Engines needing adacpp fall back to OCC "
            "when it's unavailable."
        ),
    }

    # Strict coverage: fail the conversion if any geometry falls back from the selected OCC-free
    # stream engine to OCC, instead of silently completing on OCC. Lets you enforce/measure 100%
    # libtess2 (or adacpp-*) coverage. No effect when the engine is 'occ-builtin'.
    strict_tess_option = {
        "name": "strict_tess",
        "type": "bool",
        "default": False,
        "description": (
            "Fail if the selected (non-OCC) tessellation engine can't handle a geometry and would "
            "fall back to OCC. Use to enforce/measure 100% libtess2/adacpp coverage — the "
            "conversion errors out naming the offending geometry instead of silently completing on "
            "the OCC path. No effect when the engine is 'occ-builtin'."
        ),
    }

    # Schema for FEM-source → CAD-target pairs. ``fem_to_objects`` rebuilds
    # concept Beam/Plate objects from the mesh before export — without it a
    # FEM → IFC/XML conversion is near-empty (the writers only emit concept
    # geometry). Shown on IFC/XML rows for FEM sources only.
    fem_to_objects_options = [
        {
            "name": "fem_to_objects",
            "type": "bool",
            "default": True,
            "description": (
                "Rebuild concept Beam/Plate objects from the FEM mesh "
                "before export (recommended for CAD targets). Disable to "
                "export only pre-existing concept geometry."
            ),
        },
        {
            "name": "merge_fem_objects",
            "type": "bool",
            "default": True,
            "description": (
                "Merge coplanar shell plates (same material + thickness) "
                "and colinear beams (same section + material) into single "
                "objects. Disable to keep one object per FEM element."
            ),
        },
        {
            "name": "reconstruct_surfaces",
            "type": "bool",
            "default": False,
            "description": (
                "Experimental: recover smooth structured quad panels as single "
                "curved B-spline plates instead of one flat plate per shell "
                "element — far smaller/faster CAD output for meshes generated "
                "from curved panels. Non-reconstructable regions fall back to "
                "flat plates."
            ),
        },
        {
            "name": "fem_merge_strategy",
            "type": "enum",
            "default": "auto",
            "enum": ["auto", "coplanar", "none"],
            "description": (
                "How FEM shells fold into CAD faces (STEP target). 'auto' (default) "
                "recognises each region as an analytic primitive with NO human guidance "
                "— tubular members become exact CYLINDRICAL_SURFACE faces, flat panels "
                "merge into plates (a jacket collapses from ~100k plates to a handful of "
                "cylinders in seconds). 'coplanar' forces flat-plate merging only; 'none' "
                "keeps one face per element."
            ),
        },
    ]

    # Original three targets (glb/ifc/xml) via the long-standing ada
    # writers, plus gnx — the Genie workspace the xml writer's output is
    # zipped into, so it rides every xml row.
    for ext in _ADA_LOADABLE_EXTS:
        for tgt in ("glb", "ifc", "xml", "gnx"):

            def _h(
                src,
                on_progress,
                *,
                _ext=ext,
                _tgt=tgt,
                merge_meshes=None,
                fem_to_objects=None,
                merge_fem_objects=None,
                reconstruct_surfaces=None,
                step_streamer=None,
                step_glb_pipeline=None,
                glb_tess_engine=None,
                strict_tess=None,
                serializer=None,
                tessellator=None,
                face_regions=None,
                **_kw,
            ):
                # The friendly serializer/tessellator dropdowns resolve into the existing engine
                # knobs (single-sourced by _apply_glb_serializer); explicit step_glb_pipeline /
                # glb_tess_engine still work when serializer is unset.
                step_glb_pipeline, glb_tess_engine, _force_python = _apply_glb_serializer(
                    _ext,
                    serializer,
                    tessellator,
                    step_glb_pipeline=step_glb_pipeline,
                    glb_tess_engine=glb_tess_engine,
                )
                # STEP + python serializer + Clickable surfaces => the OCC per-face clickable track.
                # (The cpp serializer routes face_regions through the native path via env, as before.)
                occ_clickable = (
                    _ext in {".step", ".stp"}
                    and _tgt == "glb"
                    and bool(face_regions)
                    and serializer == _GLB_SERIALIZER_PYTHON
                )
                return _via_ada(
                    src,
                    _ext,
                    _tgt,
                    on_progress,
                    merge_meshes=merge_meshes,
                    fem_to_objects=fem_to_objects,
                    merge_fem_objects=merge_fem_objects,
                    reconstruct_surfaces=reconstruct_surfaces,
                    step_streamer=step_streamer,
                    step_glb_pipeline=step_glb_pipeline,
                    glb_tess_engine=glb_tess_engine,
                    strict_tess=strict_tess,
                    occ_clickable=occ_clickable,
                )

            if tgt == "glb":
                # STEP sources: streaming toggle + the STEP engine selector (incl. the OCC
                # streaming reader). Other →glb sources: the BatchTessellator engine toggle.
                # strict_tess (fail-on-OCC-fallback) applies to the non-STEP BatchTessellator path.
                # The serializer/tessellator dropdowns are added on every →glb row.
                if ext in {".step", ".stp"}:
                    row_options = glb_options + [step_streamer_option, step_glb_pipeline_option, strict_tess_option]
                else:
                    row_options = glb_options + [glb_tess_engine_option, strict_tess_option]
                row_options = row_options + _glb_serializer_options(ext)
            elif tgt in ("ifc", "xml", "gnx") and ext in _FEM_SOURCE_EXTS:
                row_options = fem_to_objects_options
            else:
                row_options = None

            ConverterRegistry.register(ext, tgt, _h, options=row_options)

    # New M2 targets: stl / obj (via to_trimesh_scene) and step (via
    # to_stp). All eight ada-loadable sources support all three new
    # targets — to_trimesh_scene tessellates whatever Part the ada
    # loader returned, and to_stp re-exports through OCC.
    for ext in _ADA_LOADABLE_EXTS:
        for tgt in ("stl", "obj"):

            def _h(src, on_progress, *, _ext=ext, _tgt=tgt, **_kw):
                return _via_ada_to_trimesh(src, _ext, f".{_tgt}", on_progress)

            ConverterRegistry.register(ext, tgt, _h)

        def _step(
            src,
            on_progress,
            *,
            _ext=ext,
            fem_to_objects=None,
            merge_fem_objects=None,
            reconstruct_surfaces=None,
            fem_merge_strategy=None,
            **_kw,
        ):
            return _via_ada_to_step(
                src,
                _ext,
                on_progress,
                fem_to_objects=fem_to_objects,
                merge_fem_objects=merge_fem_objects,
                reconstruct_surfaces=reconstruct_surfaces,
                fem_merge_strategy=fem_merge_strategy,
            )

        ConverterRegistry.register(
            ext,
            "step",
            _step,
            options=(fem_to_objects_options if ext in _FEM_SOURCE_EXTS else None),
        )

    # A .SIN reaches the CAD targets through its own input deck.
    #
    # A results file is not ada-loadable — it is a binary of result records — so it
    # was offered ``fem`` and ``glb`` and nothing else, and the viewer's "export
    # this model for GeniE" was greyed out for exactly the file the results work is
    # about. But the deck IS in there: SESTRA echoes the whole Input Interface File
    # beside its results, ``_sin_to_fem`` already extracts it verbatim, and a .fem
    # is ada-loadable. So the chain is extraction followed by the ordinary FEM
    # export, with the same options and the same writer.
    #
    # Two steps rather than one because each half is already tested on its own:
    # nothing here reimplements either the extraction or the concept rebuild.
    for tgt in _GXML_TARGETS:

        def _sin_cad(
            src,
            on_progress,
            *,
            _tgt=tgt,
            fem_to_objects=None,
            merge_fem_objects=None,
            reconstruct_surfaces=None,
            **_kw,
        ):
            from ada.fem.formats.sesam.results.export_fem import export_fem_text

            on_progress("extracting input deck", 0.1)
            deck = new_temp_path(suffix=".fem")
            deck.write_text(export_fem_text(src), encoding="ascii")
            try:
                return _via_ada(
                    deck,
                    ".fem",
                    _tgt,
                    on_progress,
                    fem_to_objects=fem_to_objects,
                    merge_fem_objects=merge_fem_objects,
                    reconstruct_surfaces=reconstruct_surfaces,
                )
            finally:
                deck.unlink(missing_ok=True)

        ConverterRegistry.register(".sin", tgt, _sin_cad, options=fem_to_objects_options)
