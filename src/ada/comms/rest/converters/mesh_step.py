"""Tessellated mesh (STL / OBJ) and STEP exports via OCC, and GLB → mesh transcoding.
"""

from __future__ import annotations

import io
import pathlib
from typing import TYPE_CHECKING

from ada.core.file_system import new_temp_path

from .ada_load import (
    _FEM_SOURCE_EXTS,
    _apply_fem_to_objects,
    _load_with_ada,
    _native_ngeom_mesh_route,
)
from .keys import _GXML_SOURCE_EXTS
from .registry import ConverterRegistry, ProgressFn

if TYPE_CHECKING:
    pass


def _via_ada_to_trimesh(
    src_path: pathlib.Path,
    source_ext: str,
    target_ext: str,
    on_progress: ProgressFn,
) -> bytes | pathlib.Path:
    """Ada-loadable source → mesh export (``.stl`` / ``.obj``).

    Genie-XML sources take the fully-native NGEOM-record route when available
    (:func:`_native_ngeom_mesh_route`): adacpp tessellates and writes the OBJ/STL in C++,
    returning the file path (ownership transfers to the caller). Everything else — and any
    native fallback — bridges the same ada-loadable formats ``_via_ada`` handles to trimesh's
    mesh-only export targets via :meth:`Part.to_trimesh_scene`, so tessellation honours adapy's
    geom-repr / merge-meshes conventions and trimesh serialises the resulting scene.
    """

    on_progress("parsing", 0.15)
    model = _load_with_ada(src_path, source_ext)
    fmt = target_ext.lstrip(".").lower()
    if fmt in ("obj", "stl"):
        native_path = new_temp_path(suffix=f".{fmt}")
        native_out = _native_ngeom_mesh_route(model, source_ext, fmt, native_path, on_progress)
        if native_out is not None:
            return native_out
        native_path.unlink(missing_ok=True)  # fell back: drop the unused temp slot
    on_progress("tessellating", 0.55)
    scene = model.to_trimesh_scene()
    _strip_unexportable_for(scene, target_ext)
    _seed_empty_scene(scene)
    on_progress("exporting", 0.85)
    out = io.BytesIO()
    scene.export(file_obj=out, file_type=target_ext.lstrip("."))
    on_progress("ready", 1.0)
    return out.getvalue()


# Mesh-only export targets. trimesh's OBJ and STL writers iterate
# scene.geometry and assume every entry is a Trimesh — they break on
# Path3D (polyline-only geometries that adapy emits for line elements
# / open profiles). GLB tolerates Path3D natively so it stays
# unfiltered.
_MESH_ONLY_TARGETS: frozenset[str] = frozenset({".obj", ".stl", ".ply", ".off"})


def _strip_unexportable_for(scene, target_ext: str) -> None:
    """Drop scene entries that ``target_ext``'s writer can't handle.

    For OBJ/STL/PLY/OFF we keep only ``trimesh.Trimesh`` geometries.
    Path3D objects (line elements, open wireframes from the SAT /
    IFC importers) get filtered so the export doesn't AttributeError
    on the trimesh side. The dropped entities are recoverable in
    the GLB output if the user needs them.
    """
    import trimesh

    ext = target_ext.lower()
    if not ext.startswith("."):
        ext = "." + ext
    if ext not in _MESH_ONLY_TARGETS:
        return
    to_drop = []
    for name, geom in scene.geometry.items():
        if not isinstance(geom, trimesh.Trimesh):
            to_drop.append(name)
    for name in to_drop:
        scene.delete_geometry(name)


def _seed_empty_scene(scene) -> None:
    """Trimesh refuses to export a 0-geometry scene with
    ``"Can't export empty scenes!"`` even though every backing format
    (glb/stl/obj) is perfectly happy with zero meshes. When adapy's
    parse drops every face (SAT files containing only construction
    geometry, IFC files with only metadata, etc.) we still want a
    valid file the viewer can load and the audit log to record
    ``status=done`` — the operator dug through the audit details if
    they want to know why the scene was empty.

    Same trick :mod:`ada.fem.results.artefacts` already uses for
    line-only FEA models: seed a degenerate ``PointCloud`` of a
    single origin point so trimesh has *something* to serialise.
    """
    import numpy as np
    import trimesh

    if len(scene.geometry) > 0:
        return
    placeholder = trimesh.PointCloud(vertices=np.zeros((1, 3), dtype=np.float64))
    scene.add_geometry(placeholder, node_name="empty", geom_name="empty")


# STEP solid-root keywords — mirrors adacpp's StepNgeomStream root taxonomy
# (cadit/step/step_reader.h). Used only to detect a fully-empty OCC re-export so
# the IFC->STEP path can fall back to the faceted streaming writer.
_STEP_SOLID_ROOTS: tuple[bytes, ...] = (
    b"MANIFOLD_SOLID_BREP",
    b"SHELL_BASED_SURFACE_MODEL",
    b"BREP_WITH_VOIDS",
    b"EXTRUDED_AREA_SOLID",
    b"REVOLVED_AREA_SOLID",
    b"BOOLEAN_RESULT",
)


def _step_has_solids(path: pathlib.Path) -> bool:
    """True if the STEP file at ``path`` contains at least one solid root entity.
    Chunk-scanned (overlapping the longest keyword) so a large file isn't slurped
    whole."""
    overlap = max(len(k) for k in _STEP_SOLID_ROOTS) - 1
    tail = b""
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    return False
                window = tail + chunk
                if any(k in window for k in _STEP_SOLID_ROOTS):
                    return True
                tail = window[-overlap:]
    except OSError:
        return False


def _via_ada_to_step(
    src_path: pathlib.Path,
    source_ext: str,
    on_progress: ProgressFn,
    *,
    fem_to_objects: bool | None = None,
    merge_fem_objects: bool | None = None,
    reconstruct_surfaces: bool | None = None,
    fem_merge_strategy: str | None = None,
) -> pathlib.Path:
    """Ada-loadable source → STEP via the OCC writer.

    Primary use is the IFC → STEP interop case (no STEP writer in
    ifcopenshell itself); also exercised by .step / .stp identity
    re-exports, which can be useful for normalising a malformed STEP
    through OCC's parser.
    """

    from ada.config import logger

    on_progress("parsing", 0.15)
    model = _load_with_ada(src_path, source_ext)
    is_fem = source_ext.lower() in _FEM_SOURCE_EXTS
    if not is_fem:
        # IFC/CAD source: materialise any FEM-derived concept objects up front
        # (a no-op when there is no mesh) before the OCC writer runs.
        _apply_fem_to_objects(model, source_ext, "step", fem_to_objects, merge_fem_objects, reconstruct_surfaces)
    on_progress("writing-step", 0.55)
    out_path = new_temp_path(suffix=".step")
    returned_path = False
    try:
        if is_fem:
            # A FEM mesh rebuilds into extruded plates/straight beams, which the
            # streaming AP242 writer emits one-at-a-time at constant memory. We
            # deliberately do NOT pre-build them here: the create_objects_from_fem
            # phase (not the writer) was the multi-GB peak that OOM-killed the
            # worker on large jackets/ships — the writer fuses Beam/Plate straight
            # from the mesh. The OCC XCAF writer would instead accumulate every
            # solid plus a full entity-graph copy. fem_to_objects=False opts out.
            # Fold FEM shells via the shared object-free face engine (matches the
            # Genie-XML and IFC streamers) unless curved-plate reconstruction is
            # requested. merge_fem_objects -> coplanar/none.
            recon = bool(reconstruct_surfaces) if reconstruct_surfaces is not None else False
            ms = None
            if not recon:
                # Auto-detect analytic primitives by DEFAULT (no human guidance): tubular
                # members -> exact cylinder surfaces, flat panels -> plates. Legacy overrides:
                # a string merge_fem_objects is the strategy verbatim; merge_fem_objects=False
                # opts out of merging entirely; fem_merge_strategy 'coplanar'/'none' force those.
                strat = (fem_merge_strategy or "auto").lower()
                if isinstance(merge_fem_objects, str):
                    ms = merge_fem_objects
                elif merge_fem_objects is False:
                    ms = "none"
                elif strat == "auto":
                    ms = "cylinder"  # analytic auto-detect
                else:
                    ms = strat  # coplanar | none | cylinder
            stats = model.to_stp(
                str(out_path), writer="stream", fuse_fem=(fem_to_objects is not False), merge_strategy=ms
            )
            skipped = (stats or {}).get("skipped", 0)
            if skipped:
                logger.warning(f"streaming STEP writer skipped {skipped} non-extrudable object(s)")
        else:
            # Genie-XML fast path (default on, ADA_CAD_NATIVE_NGEOM_EXPORT=false to opt
            # out): NGEOM records -> adacpp's C++ AP242 writer instead of the OCC XCAF /
            # per-entity Python writers. Wholesale fallback below when adacpp is absent
            # or any object fails to serialize (mirrors the xml->ifc leg).
            if source_ext.lower() in _GXML_SOURCE_EXTS:
                from ada.cadit.ngeom.export import (
                    NativeExportUnsupported,
                    native_export_enabled,
                    native_ngeom_writers_available,
                )

                if native_export_enabled() and native_ngeom_writers_available():
                    try:
                        model.to_stp(str(out_path), writer="native")
                        on_progress("ready", 1.0)
                        returned_path = True
                        return out_path
                    except NativeExportUnsupported as exc:
                        logger.warning("native xml->step route unavailable (%s); using the OCC writer", exc)
            model.to_stp(str(out_path))
            if not _step_has_solids(out_path):
                # The OCC/adacpp writer emitted no solid root — e.g. an alignment
                # IfcFixedReferenceSweptAreaSolid swept over an IfcGradientCurve,
                # which has no analytic AP242 form and isn't ported to the adacpp
                # B-rep builder, so every object was skipped. Retry via the
                # kernel-free streaming writer, which tessellates such solids to a
                # faceted MANIFOLD_SOLID_BREP so no geometry is left behind.
                logger.info("OCC ifc->step produced no solids; retrying via streaming faceted writer")
                model.to_stp(str(out_path), writer="stream", fuse_fem=False)
        on_progress("ready", 1.0)
        returned_path = True
        return out_path
    finally:
        # Ownership of the STEP file transfers to the caller on success; only
        # remove it if we bailed before returning the path.
        if not returned_path:
            try:
                out_path.unlink()
            except OSError:
                pass


def _via_glb_to_trimesh(
    src_path: pathlib.Path,
    target_ext: str,
    on_progress: ProgressFn,
) -> bytes:
    """``.glb`` → mesh container (``.stl`` / ``.obj``) via trimesh.

    Pure round-trip with no ada involvement; trimesh reads GLB and
    writes whichever mesh format the target asks for. Used to make
    /convert useful as a general 3D-format swiss-knife for users who
    already have a GLB and want a downstream-friendly mesh.
    """

    import trimesh

    on_progress("loading", 0.20)
    scene = trimesh.load(str(src_path), file_type="glb")
    if not isinstance(scene, trimesh.Scene):
        # ``trimesh.load`` can return a single Trimesh for
        # one-mesh sources; wrap so the empty-scene helper has a
        # consistent shape to inspect.
        wrapped = trimesh.Scene()
        wrapped.add_geometry(scene)
        scene = wrapped
    _seed_empty_scene(scene)
    on_progress("exporting", 0.80)
    out = io.BytesIO()
    scene.export(file_obj=out, file_type=target_ext.lstrip("."))
    on_progress("ready", 1.0)
    return out.getvalue()


def _register_glb_to_mesh() -> None:
    # GLB → STL / OBJ via pure trimesh. No ada round-trip needed and
    # no ada Assembly is materialised — the user came in with a mesh
    # and wants a mesh out, in a different container.
    for tgt in ("stl", "obj"):

        def _h(src, on_progress, *, _tgt=tgt, **_kw):
            return _via_glb_to_trimesh(src, f".{_tgt}", on_progress)

        ConverterRegistry.register(".glb", tgt, _h)


# M3: FEA input-deck ↔ FEA input-deck. Six new (from, to) cells in
# one ``@converter`` call. The cartesian product registers everything
# pairwise; ``exclude_identity=True`` drops the three self-pairs
# (``.inp → .inp`` etc.). Same handler body serves every cell — it
# reads ``source_ext`` / ``target_ext`` from kwargs and lets the
# generic adapy dispatcher pick the right writer.
