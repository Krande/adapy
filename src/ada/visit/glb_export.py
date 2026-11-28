"""One glTF/GLB export path for every caller.

``Part.to_gltf``, the worker's converters (``_export_with_ada``), the browser's conversion
(``ada.cadit.wasm_convert``, run in Pyodide), the procedural compile, the asset builds and the core
jobs all write a model's GLB through :func:`export_glb`, so a script, a notebook, the browser and
the worker draw the same geometry for the same model.

The legs, in the order they are tried:

1. **The native record writer** (``adacpp.cad.stream_ngeom_to_glb``): every physical object is an
   NGEOM record (its ``solid_geom()`` serialized, a lazy shape's stored buffer, or -- for an object
   with no record of its own -- the triangles the scene writer's legs make of it, as a TRI_MESH
   record), and adacpp tessellates and writes the viewer-structured GLB (merge-by-colour
   materials, per-solid draw ranges, assembly paths, inline EXT_meshopt, metres). The model's own
   data -- the design extension, the take-off, the procedural document -- is then added to it.
2. **The Python scene writer** (``SceneConverter``) with the stream pipeline selected
   (``ADA_STREAM_TESS_PIPELINE``, libtess2 wherever adacpp is importable): for what the record
   writer cannot express -- FEM meshes, line geometry, the one-node-per-object debug layout, a
   caller's own render parameters.
3. **The CAD backend** (OCC BRepMesh through ``active_backend().build``) inside the scene writer,
   per object, where the stream kernel has no answer.

Environment overrides (unchanged meaning): ``ADAPY_GLB_TESS_ENGINE`` / ``ADA_STREAM_TESS_PIPELINE``
pick the engine, ``ADA_STREAM_TESS_STRICT`` makes a kernel fallback an error,
``ADA_CAD_NATIVE_NGEOM_EXPORT`` (config) switches the record writers off, and ``ADA_GLB_WRITER``
(``auto`` | ``native`` | ``python``) forces a writer.
"""

from __future__ import annotations

import contextlib
import io
import os
import pathlib
import tempfile
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, BinaryIO

from ada.config import logger

if TYPE_CHECKING:
    from ada.visit.render_params import RenderParams

STREAM_ENV = "ADA_STREAM_TESS_PIPELINE"
STRICT_ENV = "ADA_STREAM_TESS_STRICT"
ENGINE_ENV = "ADAPY_GLB_TESS_ENGINE"
WRITER_ENV = "ADA_GLB_WRITER"
CDT_ENV = "ADA_TESS_WT_CDT_FULL_PATCH"

#: The stream tracks the record writer tessellates with (the others are scene-writer kernels).
RECORD_TRACKS = ("libtess2", "cdt")


@dataclass
class GlbExportReport:
    """Which leg wrote a GLB, and why the record writer did not when it did not."""

    writer: str  # "native" | "python"
    pipeline: str | None = None
    reason: str | None = None  # why not native (None when native)
    objects: int = 0
    record_kinds: dict[str, int] = field(default_factory=dict)
    fallbacks: dict[str, int] = field(default_factory=dict)  # objects drawn by the scene writer's legs
    stats: dict = field(default_factory=dict)


_LAST: GlbExportReport | None = None


def last_report() -> GlbExportReport | None:
    """The report of the last :func:`export_glb` in this process (diagnostics and tests)."""
    return _LAST


# --- engine resolution ------------------------------------------------------------------------


def engine_to_stream(engine: str | None) -> str | None:
    """A ``glb_tess_engine`` token -> the stream pipeline value, or None for the backend's own
    tessellation (``occ-builtin`` / unknown). Structural, the same in every process: ``libtess2``
    -> ``libtess2``, ``adacpp-X`` -> ``X`` (``adacpp-native`` is a whole-file STEP path, not a
    track)."""
    tok = (engine or "").strip().lower()
    if tok == "libtess2":
        return "libtess2"
    if tok.startswith("adacpp-") and tok != "adacpp-native":
        return tok[len("adacpp-") :]
    return None


def default_engine() -> str:
    """``libtess2`` wherever adacpp is importable, else the backend's own (``occ-builtin``)."""
    from importlib.util import find_spec

    try:
        if find_spec("adacpp") is not None:
            return "libtess2"
    except Exception:  # noqa: BLE001 - find_spec can raise on a broken import path
        pass
    return "occ-builtin"


def resolve_stream_pipeline(glb_tess_engine: str | None = None) -> str | None:
    """The stream pipeline a GLB export tessellates with. A per-call engine wins, then the
    ``ADAPY_GLB_TESS_ENGINE`` setting, then an explicit ``ADA_STREAM_TESS_PIPELINE``, then the
    adacpp-aware default."""
    if glb_tess_engine:
        return engine_to_stream(glb_tess_engine)
    env_engine = (os.environ.get(ENGINE_ENV) or "").strip()
    if env_engine:
        return engine_to_stream(env_engine)
    ambient = (os.environ.get(STREAM_ENV) or "").strip()
    if ambient:
        return ambient
    return engine_to_stream(default_engine())


@contextlib.contextmanager
def scoped_env(values: dict[str, str | None]):
    """Set (or, for None, clear) environment variables for the duration of the block."""
    prev = {k: os.environ.get(k) for k in values}
    try:
        for k, v in values.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@contextlib.contextmanager
def export_env(model, *, glb_tess_engine: str | None = None, strict_tess: bool | None = None):
    """The environment a GLB export of ``model`` tessellates under: the stream pipeline
    (:func:`resolve_stream_pipeline`), strict coverage, and the thick-curved-shell CDT seam weld.
    :func:`export_glb` runs inside it; a caller that builds a scene writer's scene itself (the
    viewer's websocket path) wraps that in it too, so it tessellates as the export would."""
    pipeline = resolve_stream_pipeline(glb_tess_engine)
    if strict_tess is None:
        strict = os.environ.get(STRICT_ENV)  # the ambient setting stands
    else:
        strict = "1" if (strict_tess and pipeline) else None
    env = {STREAM_ENV: pipeline, STRICT_ENV: strict}
    if os.environ.get(CDT_ENV) is None and _has_thick_curved_shells(model):
        env[CDT_ENV] = "1"
    with scoped_env(env):
        yield pipeline


def _has_thick_curved_shells(model) -> bool:
    try:
        from ada import PlateCurved
        from ada.config import Config

        if not Config().geom_thicken_curved_shells:
            return False
        for p in model.get_all_parts_in_assembly(include_self=True):
            for pl in p.plates:
                if isinstance(pl, PlateCurved) and pl.t:
                    return True
    except Exception:  # noqa: BLE001 - detection never fails an export
        return False
    return False


def _has_fem_elements(model) -> bool:
    try:
        return any(len(p.fem.elements) > 0 for p in model.get_all_parts_in_assembly(include_self=True))
    except Exception:  # noqa: BLE001 - a malformed FEM container: let the scene writer decide
        return True


# --- the record writer ------------------------------------------------------------------------

#: RenderParams fields the record writer honours. Any other field set away from its default needs
#: the scene writer (post-processors, effects, render overrides, filters, ...).
_NATIVE_PARAM_FIELDS = {
    "embed_object_metadata",
    "embed_model_stats",
    "embed_procedural_doc",
    "merge_meshes",
    "stream_from_ifc_store",
    "auto_sync_ifc_store",
    "purpose",
    "scene",
    "fea_params",
    "unique_id",
    "serve_web_port",
    "serve_ws_port",
    "serve_html",
    "add_ifc_backend",
    "backend_file_dir",
    "gltf_export_to_file",
}


def _params_blocker(params) -> str | None:
    if params is None:
        return None
    from dataclasses import fields

    from ada.visit.render_params import RenderParams

    ref = RenderParams()
    for f in fields(RenderParams):
        if f.name in _NATIVE_PARAM_FIELDS:
            continue
        if getattr(params, f.name) != getattr(ref, f.name):
            return f"render parameter {f.name.lstrip('_')!r} needs the scene writer"
    return None


def native_blocker(model, *, params=None, merge_meshes: bool = True, pipeline: str | None, writer: str | None = None):
    """Why the record writer cannot write this model's GLB, or None when it can."""
    from ada import Part

    choice = (writer or os.environ.get(WRITER_ENV) or "auto").strip().lower()
    if choice == "python":
        return f"{WRITER_ENV}=python" if not writer else "the caller asked for the Python writer"
    if not isinstance(model, Part):
        return f"{type(model).__name__} is not a Part/Assembly"
    from ada.cadit.ngeom.export import (
        native_export_enabled,
        native_mesh_writers_available,
    )

    if not native_export_enabled():
        return "the native NGEOM export is switched off (ADA_CAD_NATIVE_NGEOM_EXPORT)"
    if not native_mesh_writers_available():
        return "this adacpp has no NGEOM-record GLB writer"
    if pipeline not in RECORD_TRACKS:
        return f"tessellation engine {pipeline or 'occ-builtin'!r} is not a record-writer track"
    if not merge_meshes:
        return "one node per object (merge_meshes=False) is a scene-writer layout"
    blocked = _params_blocker(params)
    if blocked:
        return blocked
    if _has_fem_elements(model):
        return "FEM elements (shell faces, beam lines, the analysis extension) are drawn by the scene writer"
    return None


def _scene_mesh_record(obj):
    """An object with no NGEOM record of its own, tessellated by the scene writer's legs (stream
    kernel, then the CAD backend) into one world-space TRI_MESH record: ``(blob, None)``; False
    when it draws nothing (the scene writer would drop it too); None when it is line geometry,
    which the record writer cannot draw."""
    import numpy as np

    from ada.cadit.ngeom.serialize import _Encoder
    from ada.visit.gltf.meshes import MeshType
    from ada.visit.tessellate import BatchTessellator

    pos, tris, base = [], [], 0
    for ms in BatchTessellator().batch_tessellate([obj]):
        if ms.type != MeshType.TRIANGLES:
            return None
        p = np.asarray(ms.position, dtype=float).reshape(-1, 3)
        if ms.indices is None or len(ms.indices) == 0 or len(p) == 0:
            continue
        pos.append(p)
        tris.append(np.asarray(ms.indices, dtype=np.int64).reshape(-1, 3) + base)
        base += len(p)
    if not pos:
        return False
    enc = _Encoder(mesh_kinds=True)
    idx = enc.tri_mesh(np.vstack(pos), np.vstack(tris))
    return enc.finish([(idx, str(obj.name))]), None


def _drew_nothing(rec, pipeline: str) -> bool:
    from ada.cad import active_backend
    from ada.cad.registry import stream_tess_defaults, stream_tess_model_scale

    be = active_backend()
    if not hasattr(be, "tessellate_stream_buffer"):
        return False
    defl, ang = stream_tess_defaults()
    try:
        bm = be.tessellate_stream_buffer(
            rec[1], pipeline=pipeline, deflection=defl, angular_deg=ang, model_scale=stream_tess_model_scale()
        )
    except Exception:  # noqa: BLE001 - a record the kernel throws on drew nothing
        return True
    idx = getattr(bm, "indices", None)
    return idx is None or len(idx) == 0 or int(getattr(bm, "mesh_type", 4)) != 4


def _write_native(
    model, out_path: pathlib.Path, pipeline: str, *, meshopt: bool, report: GlbExportReport, extra_meshes=None
):
    """Write the GLB with the record writer; raises NativeExportUnsupported to fall back."""
    import adacpp

    from ada.cadit.ngeom.export import (
        NativeExportUnsupported,
        _check_mesh_emit_stats,
        _stream_tess_params,
        _unit_scale,
        collect_ngeom_records,
    )
    from ada.cadit.ngeom.serialize import tri_mesh_supported

    meshes = tri_mesh_supported()
    lines: list[str] = []

    def on_unsupported(obj, reason):
        got = _scene_mesh_record(obj)
        if got is None:
            lines.append(f"{type(obj).__name__} {obj.name!r}")
        return got

    walk: dict = {}
    records, counts = collect_ngeom_records(
        model, mesh_kinds=meshes, on_unsupported=on_unsupported if meshes else None, stats_out=walk
    )
    if lines:
        raise NativeExportUnsupported(f"line geometry the GLB writer cannot draw: {lines[:5]}")
    if extra_meshes:
        if not meshes:
            raise NativeExportUnsupported("extra meshes need TRI_MESH records, which this adacpp lacks")
        from ada.cadit.ngeom.serialize import _Encoder

        for k, (name, pos, tri, rgba) in enumerate(extra_meshes):
            enc = _Encoder(mesh_kinds=True)
            idx = enc.tri_mesh(pos, tri)
            col = None if rgba is None else [float(c) for c in rgba]
            path = [[(1, str(model.name)), (3_000_000 + k, str(name))]]  # under the root, as add_geometry puts it
            records.append((str(name), enc.finish([(idx, str(name))]), col, None, path))
            walk.setdefault("record_kinds", {})["TRI_MESH (extra mesh)"] = (
                walk.get("record_kinds", {}).get("TRI_MESH (extra mesh)", 0) + 1
            )

    def emit(recs):
        return adacpp.cad.stream_ngeom_to_glb(
            recs,
            str(out_path),
            meshopt=meshopt,
            pipeline=pipeline,
            unit_scale=_unit_scale(model),
            **_stream_tess_params(),
        )

    stats = emit(records)
    if stats.get("solids_skipped") and meshes:
        # A record the kernel drew nothing for: draw that object through the scene writer's legs
        # instead (stream kernel, then the CAD backend) and write the GLB again.
        owners = walk.get("objects", [])
        replaced = 0
        for i, rec in enumerate(records):
            if i < len(owners) and _drew_nothing(rec, pipeline):
                got = _scene_mesh_record(owners[i])
                if got is None:
                    raise NativeExportUnsupported(f"{owners[i].name!r} is line geometry the GLB writer cannot draw")
                if got is False:
                    continue
                records[i] = (rec[0], got[0], rec[2], got[1], rec[4])
                key = "kernel drew nothing for the record"
                walk["fallbacks"][key] = walk["fallbacks"].get(key, 0) + 1
                replaced += 1
        if replaced:
            stats = emit(records)
    _check_mesh_emit_stats(stats, "GLB")
    stats.update(counts)
    report.objects = counts.get("objects", 0)
    report.record_kinds = dict(walk.get("record_kinds", {}))
    report.fallbacks = dict(walk.get("fallbacks", {}))
    report.stats = {k: v for k, v in stats.items() if not isinstance(v, (dict, list))}
    return stats


def _finish_native(model, glb_path: pathlib.Path, params) -> None:
    """Give the record writer's GLB the model data the scene writer's carries: the design block of
    ``ADA_EXT_data``, the assembly guid, and ``asset.extras`` (take-off, procedural document)."""
    from ada.cadit.ngeom.export import _add_design_extension
    from ada.visit.render_params import RenderParams
    from ada.visit.scene_converter import SceneConverter

    params = params or RenderParams()
    extras: dict = {}
    conv = SceneConverter(model, params)
    if params.embed_model_stats:
        stats = conv.build_model_stats()
        if stats is not None:
            extras["model_stats"] = stats
    if params.embed_procedural_doc:
        doc = conv.build_procedural_doc()
        if doc is not None:
            extras["procedural_doc"] = doc
    if params.gltf_asset_extras_dict:
        extras.update(params.gltf_asset_extras_dict)
    try:
        guid = model.get_assembly().guid
    except Exception:  # noqa: BLE001 - a part outside an assembly has no lineage anchor
        guid = None
    _add_design_extension(
        model,
        glb_path,
        embed_metadata=bool(params.embed_object_metadata),
        assembly_guid=guid,
        asset_extras=extras or None,
    )


# --- the entry point --------------------------------------------------------------------------


def export_glb(
    model,
    dest: str | os.PathLike | BinaryIO | None = None,
    *,
    params: RenderParams | None = None,
    merge_meshes: bool = True,
    glb_tess_engine: str | None = None,
    strict_tess: bool | None = None,
    meshopt: bool = True,
    writer: str | None = None,
    extra_meshes: list[tuple] | None = None,
) -> bytes | None:
    """Write ``model``'s GLB through the shared path (see the module docstring).

    ``extra_meshes``: ``(name, positions (n, 3), triangles (m, 3), rgba | None)`` world-space meshes
    drawn beside the model's objects (e.g. the CAD bodies a procedural compile splices in) -- as
    TRI_MESH records on the record writer, as scene geometry on the scene writer.

    ``dest``: a path, a binary file object, or None to get the bytes back. ``params``: the scene
    writer's render parameters; any that only the scene writer can honour route the export there.
    ``glb_tess_engine``: an engine token (``libtess2``, ``adacpp-cdt``, ``occ-builtin``, ...), else
    the configured default. ``strict_tess``: a stream-kernel fallback is an error. ``meshopt``:
    EXT_meshopt-compress the record writer's buffers. ``writer``: ``"native"`` / ``"python"`` to
    force a leg (``ADA_GLB_WRITER`` likewise).

    Raises ``ValueError("Can't export empty scenes!")`` (trimesh's) when the model has nothing to
    draw, as ``to_gltf`` always has.
    """
    global _LAST
    pipeline = resolve_stream_pipeline(glb_tess_engine)
    report = GlbExportReport(writer="python", pipeline=pipeline)

    data: bytes | None = None
    with export_env(model, glb_tess_engine=glb_tess_engine, strict_tess=strict_tess):
        reason = native_blocker(model, params=params, merge_meshes=merge_meshes, pipeline=pipeline, writer=writer)
        if reason is None:
            from ada.cadit.ngeom.export import NativeExportUnsupported

            tmp = pathlib.Path(tempfile.mkdtemp(prefix="ada_glb_")) / "out.glb"
            try:
                _write_native(model, tmp, pipeline, meshopt=meshopt, report=report, extra_meshes=extra_meshes)
                _finish_native(model, tmp, params)
                data = tmp.read_bytes()
                report.writer = "native"
            except NativeExportUnsupported as exc:
                reason = str(exc)
            except Exception as exc:  # noqa: BLE001 - the scene writer is always there to fall back on
                if strict_tess:
                    raise
                reason = f"record writer failed: {type(exc).__name__}: {exc}"
            finally:
                with contextlib.suppress(OSError):
                    tmp.unlink()
                with contextlib.suppress(OSError):
                    tmp.parent.rmdir()
        if data is None:
            report.reason = reason
            if reason and (writer or os.environ.get(WRITER_ENV, "")).strip().lower() == "native":
                raise RuntimeError(f"native GLB writer requested but unavailable: {reason}")
            logger.debug("GLB export through the scene writer: %s", reason)
            data = _write_scene(model, params, merge_meshes, extra_meshes)

    _LAST = report
    if dest is None:
        return data
    if isinstance(dest, (str, os.PathLike)):
        path = pathlib.Path(dest)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    else:
        dest.write(data)
    return None


def _write_scene(model, params, merge_meshes: bool, extra_meshes=None) -> bytes:
    from ada.visit.render_params import RenderParams
    from ada.visit.scene_converter import SceneConverter

    if params is None:
        params = RenderParams(stream_from_ifc_store=False, merge_meshes=merge_meshes)
    conv = SceneConverter(model, params)
    if not extra_meshes:
        return conv.build_glb()
    import trimesh

    scene = conv.build_processed_scene()
    for name, pos, tri, rgba in extra_meshes:
        mesh = trimesh.Trimesh(vertices=pos, faces=tri, process=False)
        if rgba is not None:
            mesh.visual = trimesh.visual.TextureVisuals(
                material=trimesh.visual.material.PBRMaterial(baseColorFactor=rgba)
            )
        scene.add_geometry(mesh, node_name=str(name))
    return conv.build_glb()


def native_glb_bytes(model, params: RenderParams | None = None) -> bytes | None:
    """The record writer's GLB of ``model`` under ``params``, or None where only the scene writer
    can draw it (the caller then builds its trimesh scene as before)."""
    from ada import Part

    if not isinstance(model, Part):
        return None
    merge = True if params is None else bool(params.merge_meshes)
    if native_blocker(model, params=params, merge_meshes=merge, pipeline=resolve_stream_pipeline()) is not None:
        return None
    try:
        return export_glb(model, None, params=params, merge_meshes=merge, writer="native")
    except RuntimeError as exc:
        logger.debug("record writer unavailable for %r: %s", getattr(model, "name", model), exc)
        return None


def export_glb_bytes(model, **kwargs) -> bytes:
    """:func:`export_glb` into memory."""
    buf = io.BytesIO()
    export_glb(model, buf, **kwargs)
    return buf.getvalue()
