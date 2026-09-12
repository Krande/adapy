"""Loading a source into an ada Assembly (with the content-hashed parse cache), the FEM→concept
object rebuild, and the native NGEOM mesh route.
"""

from __future__ import annotations

import io
import pathlib
import tempfile
from typing import TYPE_CHECKING

from .keys import _FALSE, _GXML_SOURCE_EXTS
from .pipelines import _glb_engine_stream_value
from .registry import ProgressFn, UnsupportedFormat

if TYPE_CHECKING:
    pass


def _passthrough(src_path: pathlib.Path, on_progress: ProgressFn) -> bytes:
    on_progress("ready", 1.0)
    return src_path.read_bytes()


def _via_trimesh(src_path: pathlib.Path, ext: str, on_progress: ProgressFn) -> bytes:
    import trimesh

    on_progress("loading", 0.2)
    scene = trimesh.load(str(src_path), file_type=ext.lstrip("."))
    on_progress("exporting", 0.8)
    out = io.BytesIO()
    scene.export(file_obj=out, file_type="glb")
    on_progress("ready", 1.0)
    return out.getvalue()


def _via_gltf_to_glb(src_path: pathlib.Path, on_progress: ProgressFn) -> bytes:
    """glTF (text JSON) → GLB (binary). trimesh handles this round-trip."""
    return _via_trimesh(src_path, ".gltf", on_progress)


# Bumped when the pickled Assembly schema changes incompatibly, so stale cache entries from
# older code are ignored rather than mis-loaded.
_ASM_CACHE_VERSION = "1"


def _asm_cache_path(src_path: pathlib.Path, ext: str) -> pathlib.Path | None:
    """Local pickle-cache path for a parsed source, keyed by content hash — or None when the
    cache is disabled (ADA_ASSEMBLY_CACHE unset/falsy). Same content → same key, so every export
    target of one audit source reuses the first parse instead of re-reading the file."""
    import os

    if (os.environ.get("ADA_ASSEMBLY_CACHE") or "").strip().lower() in _FALSE | {""}:
        return None
    import hashlib

    h = hashlib.sha1()  # noqa: S324 - cache key, not security
    h.update(_ASM_CACHE_VERSION.encode())
    h.update(ext.encode())
    with open(src_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    cache_dir = pathlib.Path(os.environ.get("ADA_ASSEMBLY_CACHE_DIR") or (tempfile.gettempdir() + "/ada_asm_cache"))
    return cache_dir / f"{h.hexdigest()}.pkl"


def _load_with_ada(src_path: pathlib.Path, ext: str):
    import ada
    from ada.config import logger

    # Reuse a previously-parsed Assembly (read-once-export-many): the audit converts one source to
    # several targets, and re-reading/re-parsing the same file per target is pure overhead. Each
    # hit returns a fresh deep copy via from_pickle, so per-target mutation never cross-contaminates.
    cache_path = _asm_cache_path(src_path, ext)
    if cache_path is not None and cache_path.exists():
        try:
            return ada.from_pickle(cache_path)
        except Exception as exc:  # noqa: BLE001 - corrupt / version-mismatched cache → re-parse
            logger.debug("assembly cache miss (unreadable %s): %s", cache_path, exc)

    def _read():
        if ext == ".ifc":
            return ada.from_ifc(src_path)
        if ext in {".step", ".stp"}:
            return ada.from_step(src_path)
        if ext in _GXML_SOURCE_EXTS:
            return ada.from_genie_xml(src_path)
        if ext in {".inp", ".fem"}:
            return ada.from_fem(src_path)
        if ext in {".sat", ".acis"}:
            return ada.from_acis(src_path)
        raise UnsupportedFormat(f"ada path does not handle {ext!r}")

    model = _read()
    if cache_path is not None:
        try:
            model.to_pickle(cache_path)
        except Exception as exc:  # noqa: BLE001 - caching is best-effort; never fail the conversion
            logger.debug("assembly cache store failed (%s): %s", cache_path, exc)
    return model


# FEM source extensions that carry a mesh (nodes + elements) rather than
# concept geometry, and the CAD targets where rebuilding concept objects
# from that mesh is worthwhile.
_FEM_SOURCE_EXTS: frozenset[str] = frozenset({".inp", ".fem", ".sif"})


_FEM_OBJECT_CAD_TARGETS: frozenset[str] = frozenset({"ifc", "xml", "gnx", "step", "stp"})


# The Genie targets: a concept XML, or the same XML zipped into a workspace
# (.gnx) with its ACIS body beside it. One writer family; every routing rule
# that says "xml" means both.
_GXML_TARGETS: frozenset[str] = frozenset({"xml", "gnx"})


def _apply_fem_to_objects(
    model,
    source_ext: str,
    target_format: str,
    fem_to_objects: bool | None,
    merge_fem_objects: bool | None = None,
    reconstruct_surfaces: bool | None = None,
) -> None:
    """Rebuild concept Beam/Plate objects from a FEM mesh before a CAD
    export.

    A Sesam/Abaqus FEM deck is a mesh (nodes + shell/line elements); the
    IFC / Genie-XML / STEP writers only emit *concept* objects, so without
    this step a FEM → CAD conversion produces almost-empty output. Gated by
    the per-job ``fem_to_objects`` option (default ``True``). No-op for
    non-FEM sources, mesh targets (glb/stl/obj), or an explicit opt-out.

    ``merge_fem_objects`` (default ``True``) merges coplanar shell plates
    and colinear beams of matching section/material so the export isn't a
    cloud of one-object-per-element geometry.

    ``reconstruct_surfaces`` (default ``False``, opt-in) recovers smooth
    structured quad panels as single curved B-spline plates instead of one
    flat plate per element — a large size/time reduction for meshes generated
    from curved panels. Non-reconstructable elements fall back to flat plates.
    """
    if fem_to_objects is False:
        return
    if source_ext.lower() not in _FEM_SOURCE_EXTS:
        return
    if target_format not in _FEM_OBJECT_CAD_TARGETS:
        return
    merge = True if merge_fem_objects is None else bool(merge_fem_objects)
    recon = bool(reconstruct_surfaces) if reconstruct_surfaces is not None else False
    # The IFC streaming writer fuses shell elements into plates one at a time
    # (Part.iter_objects_from_fem), so leave plates unbuilt — build beams only —
    # and let the writer stream them, keeping peak memory bounded. The Genie-XML
    # streaming writer does the same via the object-free vectorized face source
    # (mesh_faces). Curved-plate reconstruction (advanced faces) isn't handled by
    # either text emitter, so it still takes the full build.
    skip_plates = False
    if target_format == "ifc" and not recon:
        import os

        skip_plates = os.environ.get("ADA_IFC_STREAMING", "").strip().lower() not in _FALSE
    elif _gxml_face_streaming(source_ext, target_format, recon):
        skip_plates = True
    model.create_objects_from_fem(merge=merge, reconstruct_surfaces=recon, skip_plates=skip_plates)


def _gxml_face_streaming(source_ext: str, target_format: str, reconstruct_surfaces: bool) -> bool:
    """Whether FEM→Genie-XML streams plates from the object-free vectorized face
    source instead of materialising Plate objects.

    Gated by ``ADA_GXML_STREAMING`` (default on; only an explicit falsy value
    reverts to the full object build + DOM writer). Not used for curved-plate
    reconstruction (the parametric face emitter can't express advanced faces).
    Shared by ``_apply_fem_to_objects`` (skip the plate build) and
    ``_export_with_ada`` (use the streaming writer) so they stay consistent."""
    if target_format not in _GXML_TARGETS:
        return False
    if source_ext.lower() not in _FEM_SOURCE_EXTS:
        return False
    if reconstruct_surfaces:
        return False
    import os

    return os.environ.get("ADA_GXML_STREAMING", "").strip().lower() not in _FALSE


def _native_ngeom_mesh_route(
    model,
    source_ext: str | None,
    target_format: str,
    out_path: pathlib.Path,
    on_progress: ProgressFn,
    *,
    glb_tess_engine: str | None = None,
) -> pathlib.Path | None:
    """Fully-native mesh leg for ada-object sources: serialize each object's ``solid_geom()`` to
    an NGEOM record and let adacpp tessellate + write the GLB / OBJ / STL in C++
    (``stream_ngeom_to_glb`` / ``stream_ngeom_to_mesh``) — no Python scene assembly, no trimesh
    writer (the hull's 137 s xml→obj becomes the same class as the native step→obj leg).

    Returns ``out_path`` on success, or ``None`` to fall back WHOLESALE to the Python path:
    Genie-XML sources only (concept objects with parametric ``solid_geom()``), gated by the same
    ``Config().cad_native_ngeom_export`` switch as the xml→step/ifc legs, and only when the
    requested tessellation engine is an adacpp record track. A model carrying FEM mesh content
    falls back too — the Python ``to_gltf`` renders the FEM mesh itself (shell faces, beam
    lines, the beam_solids sidecar), which the concept-object record walk cannot express. The
    zero-renderable-object case also falls back (``collect_ngeom_records`` raises), preserving
    the Python path's seeded empty-scene output.
    """
    if source_ext is None or source_ext.lower() not in _GXML_SOURCE_EXTS:
        return None
    # Engine choice must resolve to an adacpp record-stream track; occ-builtin / the taxonomy
    # kernels (occ/cgal/hybrid) mean the user asked for a different tessellator — honour it.
    stream = _glb_engine_stream_value(glb_tess_engine)
    if stream not in ("libtess2", "cdt"):
        return None
    from ada.cadit.ngeom.export import (
        NativeExportUnsupported,
        native_export_enabled,
        native_mesh_writers_available,
        native_to_glb,
        native_to_mesh,
    )
    from ada.config import logger

    if not (native_export_enabled() and native_mesh_writers_available()):
        return None
    try:
        # Renderable FEM = elements. Bare nodes (Genie support points / mass nodes) don't
        # produce mesh geometry, so they must not disqualify the native route.
        if any(len(p.fem.elements) > 0 for p in model.get_all_parts_in_assembly(include_self=True)):
            return None
    except Exception:  # noqa: BLE001 - a malformed FEM container must not kill the conversion
        return None
    try:
        on_progress("native-ngeom-tessellating", 0.55)
        if target_format == "glb":
            native_to_glb(model, out_path, pipeline=stream)
        else:
            native_to_mesh(model, out_path, target_format, pipeline=stream)
        on_progress("ready", 1.0)
        return out_path
    except NativeExportUnsupported as exc:
        logger.warning("native xml->%s route unavailable (%s); using the Python writer", target_format, exc)
    except Exception as exc:  # noqa: BLE001 - wholesale fallback: never fail the job on the fast path
        logger.warning("native xml->%s route failed (%s); using the Python writer", target_format, exc)
    return None


def _model_has_thick_curved_shells(model) -> bool:
    """True when the model carries a thickened curved-shell plate (``PlateCurved`` with
    thickness). Those are the faces whose analytic ClosedShell grows a cap<->wall seam —
    the case ``ADA_TESS_WT_CDT_FULL_PATCH`` welds watertight. Short-circuits on the first
    match; any walk failure returns False so seam-weld selection never fails a conversion."""
    try:
        from ada import PlateCurved
        from ada.config import Config

        if not Config().geom_thicken_curved_shells:
            return False
        for p in model.get_all_parts_in_assembly(include_self=True):
            for pl in p.plates:
                if isinstance(pl, PlateCurved) and pl.t:
                    return True
    except Exception:  # noqa: BLE001 - detection must never fail the conversion
        return False
    return False
