"""Native NGEOM-record export: Assembly -> STEP / IFC via adacpp's C++ emitters.

Walks the assembly, serializes each physical object's ``solid_geom()`` to a per-object NGEOM
blob (the same neutral wire the stream tessellator consumes) and hands ``(name, blob, color,
transforms, paths)`` records to ``adacpp.cad.stream_ngeom_to_step`` / ``stream_ngeom_to_ifc``.
This replaces the per-entity Python writers (ifcopenshell / ap242_stream, ~ms/face) with the
~µs/face C++ emitters — on a hull model with thickened curved shells the emit drops from tens
of seconds to seconds.

Coverage rule ("no geometry left behind"): if ANY object fails to serialize, the export raises
:class:`NativeExportUnsupported` and the caller falls back WHOLESALE to the Python writer — a
partially-native file silently missing solids is never produced. ``MassPoint`` (no geometry)
is exempt. The serialize phase collects all records up front (per-object blobs are compact —
KBs each) so the fallback decision is made before any output file exists; the C++ side then
streams them one at a time at bounded memory.

Preserved: object names, presentation colours, units (header SI length unit), and the part
hierarchy — as a NEXT_ASSEMBLY_USAGE_OCCURRENCE product tree (STEP) / nested IfcSpatialZone
tree (IFC). IFC GlobalIds are writer-generated (deterministic per run), not the ada guids —
same property as the existing ``blobs_to_ifc`` native writer.
"""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING

from ada.config import Config, logger

if TYPE_CHECKING:
    from ada.api.spatial.part import Part


class NativeExportUnsupported(Exception):
    """The model (or environment) can't take the native NGEOM export path; fall back wholesale."""


def native_ngeom_writers_available() -> bool:
    """True if adacpp's NGEOM-record emitters (stream_ngeom_to_step/ifc) are importable."""
    try:
        import adacpp

        return hasattr(adacpp.cad, "stream_ngeom_to_step") and hasattr(adacpp.cad, "stream_ngeom_to_ifc")
    except Exception:
        return False


def native_export_enabled() -> bool:
    """The config switch for the converter legs (env ``ADA_CAD_NATIVE_NGEOM_EXPORT``)."""
    return bool(Config().cad_native_ngeom_export)


def native_mesh_writers_available() -> bool:
    """True if adacpp's NGEOM-record mesh emitters (stream_ngeom_to_glb / stream_ngeom_to_mesh)
    are importable."""
    try:
        import adacpp

        return hasattr(adacpp.cad, "stream_ngeom_to_glb") and hasattr(adacpp.cad, "stream_ngeom_to_mesh")
    except Exception:
        return False


def _object_color_rgba(obj) -> list[float] | None:
    col = getattr(obj, "color", None)
    if col is None:
        return None
    try:
        r, g, b, a = tuple(col)
    except Exception:
        return None
    return [float(r), float(g), float(b), float(a)]


def collect_ngeom_records(part: Part) -> tuple[list[tuple], dict]:
    """Serialize every physical object under ``part`` to one NGEOM record.

    Returns ``(records, stats)`` where records are ``(name, blob, rgba|None, None, [path])``
    tuples for the adacpp emitters and stats counts the walk. Raises
    :class:`NativeExportUnsupported` if any geometric object can't be serialized (listing the
    per-type reasons), so the caller can fall back wholesale to the Python writer.
    """
    from ada.api.mass import MassPoint
    from ada.cadit.ngeom.serialize import _Encoder, _Unsupported

    records: list[tuple] = []
    unsupported: dict[str, int] = {}
    part_ids: dict[int, int] = {}  # id(Part) -> stable per-run int for the path rep-id slot
    n_objects = 0

    def _path_for(obj) -> list[tuple[int, str]]:
        # Root-first (rep_id, name) levels; the last level is the object's own (leaf) product.
        chain = []
        p = getattr(obj, "parent", None)
        while p is not None:
            chain.append(p)
            p = getattr(p, "parent", None)
        levels: list[tuple[int, str]] = []
        for prt in reversed(chain):
            pid = part_ids.setdefault(id(prt), len(part_ids) + 1)
            levels.append((pid, str(prt.name)))
        leaf_id = len(part_ids) + 1000000 + len(records)  # unique, disjoint from part ids
        levels.append((leaf_id, str(obj.name)))
        return levels

    for obj in part.get_all_physical_objects(pipe_to_segments=True):
        if isinstance(obj, MassPoint):
            continue  # no geometry — the Python writers skip these too
        n_objects += 1
        try:
            geom = obj.solid_geom()
        except Exception as ex:  # no parametric solid_geom (e.g. raw-OCC import)
            unsupported[f"{type(obj).__name__}: {type(ex).__name__}: {ex}"] = (
                unsupported.get(f"{type(obj).__name__}: {type(ex).__name__}: {ex}", 0) + 1
            )
            continue
        enc = _Encoder()
        try:
            idx = enc.root(geom)
        except _Unsupported as ex:
            inner = getattr(geom, "geometry", geom)
            key = f"{type(inner).__name__}: {ex}"
            unsupported[key] = unsupported.get(key, 0) + 1
            continue
        blob = enc.finish([(idx, str(obj.name))])
        records.append((str(obj.name), blob, _object_color_rgba(obj), None, [_path_for(obj)]))

    if unsupported:
        n_bad = sum(unsupported.values())
        raise NativeExportUnsupported(
            f"native NGEOM export: {n_bad}/{n_objects} object(s) not serializable — falling back "
            f"to the Python writer. Reasons: {unsupported}"
        )
    if not records:
        raise NativeExportUnsupported("native NGEOM export: model has no serializable physical objects")
    stats = {"objects": n_objects, "records": len(records)}
    return records, stats


def _unit_scale(part: Part) -> float:
    from ada.base.units import Units

    return 0.001 if part.units == Units.MM else 1.0


def native_to_stp(part: Part, out_path: str | pathlib.Path) -> dict:
    """Write ``part`` to AP242 STEP via adacpp's NGEOM-record emitter. Returns the audit dict."""
    if not native_ngeom_writers_available():
        raise NativeExportUnsupported("adacpp with stream_ngeom_to_step is not importable")
    import adacpp

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    records, walk = collect_ngeom_records(part)
    stats = adacpp.cad.stream_ngeom_to_step(records, str(out_path), unit_scale=_unit_scale(part))
    stats.update(walk)
    if stats.get("solids_skipped"):
        raise NativeExportUnsupported(
            f"native STEP emit skipped {stats['solids_skipped']} solid(s): {stats.get('drop_reasons')}"
        )
    logger.info("native NGEOM->STEP: %s", stats)
    return stats


def _check_mesh_emit_stats(stats: dict, what: str) -> None:
    """No geometry left behind: any solid the tessellator skipped, or any dropped face
    (the [GEOMHEALTH-JSON] counter), fails the native path so the caller falls back
    WHOLESALE to the Python writer instead of shipping a silently-partial mesh."""
    if stats.get("solids_skipped"):
        raise NativeExportUnsupported(
            f"native {what} emit skipped {stats['solids_skipped']} solid(s): {stats.get('drop_reasons')}"
        )
    if stats.get("faces_dropped"):
        raise NativeExportUnsupported(
            f"native {what} emit dropped {stats['faces_dropped']} face(s): {stats.get('drop_reasons')}"
        )


def _stream_tess_params() -> dict:
    """Tessellation-density knobs for the record emitters, sourced from the SAME single source of
    truth as the Python scene/stream path (``ada.cad.registry.stream_tess_defaults`` +
    ``stream_tess_model_scale``) — so the native route produces the same density the Python
    ``to_gltf`` / ``to_trimesh_scene`` legs would have, and one nominal config can't mean
    different densities on different call paths."""
    from ada.cad.registry import stream_tess_defaults, stream_tess_model_scale

    deflection, angular_deg = stream_tess_defaults()
    return {"deflection": deflection, "angular_deg": angular_deg, "model_scale": stream_tess_model_scale()}


def native_to_glb(
    part: Part, out_path: str | pathlib.Path, *, meshopt: bool = True, pipeline: str = "libtess2"
) -> dict:
    """Write ``part`` to a viewer-structured GLB via adacpp's NGEOM-record tessellate+emit core
    (merge-by-colour materials, per-solid draw ranges, inline EXT_meshopt when ``meshopt``).
    Output is metres regardless of the model units. Returns the audit dict."""
    if not native_mesh_writers_available():
        raise NativeExportUnsupported("adacpp with stream_ngeom_to_glb is not importable")
    import adacpp

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    records, walk = collect_ngeom_records(part)
    stats = adacpp.cad.stream_ngeom_to_glb(
        records,
        str(out_path),
        meshopt=meshopt,
        pipeline=pipeline,
        unit_scale=_unit_scale(part),
        **_stream_tess_params(),
    )
    stats.update(walk)
    _check_mesh_emit_stats(stats, "GLB")
    try:
        _add_design_extension(part, out_path)
    except Exception as exc:  # noqa: BLE001 - the geometry is written; its model data is a nicety
        logger.warning("native NGEOM->GLB: design extension not added: %s", exc)
    logger.info("native NGEOM->GLB: %s", stats)
    return stats


_GLB_MAGIC = b"glTF"
_GLB_JSON_CHUNK = b"JSON"


def _add_design_extension(part: Part, glb_path: pathlib.Path) -> None:
    """Give a native GLB the design block of its ``ADA_EXT_data`` the Python writer gives its own.

    The C++ emitter writes geometry, names and colours and leaves ``design_objects`` empty, which
    left a Genie model in the viewer without its groups, per-object properties and FEM concepts
    (masses, supports, loads, equipment) -- they exist only on the Python side. Only the JSON
    chunk is rewritten; the binary chunk is copied through.

    The GLB is in metres whatever the model's units, and the FEM concepts are in model
    coordinates, so a model in other units goes without them rather than with glyphs in the
    wrong place.
    """
    import json
    import struct

    from ada.visit.scene_handling.scene_from_part import build_design_data_extension

    data = glb_path.read_bytes()
    if len(data) < 20 or data[:4] != _GLB_MAGIC:
        raise ValueError("not a GLB")
    json_len, chunk_type = struct.unpack_from("<I4s", data, 12)
    if chunk_type != _GLB_JSON_CHUNK:
        raise ValueError("first GLB chunk is not JSON")
    doc = json.loads(data[20 : 20 + json_len].decode("utf-8"))
    ada_ext = doc.setdefault("extensions", {}).setdefault("ADA_EXT_data", {})
    if ada_ext.get("design_objects"):
        return

    face_nodes = [n["name"] for n in doc.get("nodes", []) if "mesh" in n and n.get("name")]
    design = build_design_data_extension(part, face_nodes=face_nodes)
    if _unit_scale(part) != 1.0 and design.fem_concepts is not None:
        logger.info("native NGEOM->GLB: FEM concepts left out of a model not in metres")
        design.fem_concepts = None
    ada_ext["design_objects"] = [design.model_dump(mode="json", exclude_none=True)]
    used = doc.setdefault("extensionsUsed", [])
    if "ADA_EXT_data" not in used:
        used.append("ADA_EXT_data")

    body = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    body += b" " * (-len(body) % 4)  # JSON chunks pad with spaces
    rest = data[20 + json_len :]
    out = bytearray(_GLB_MAGIC)
    out += struct.pack("<II", 2, 12 + 8 + len(body) + len(rest))
    out += struct.pack("<I4s", len(body), _GLB_JSON_CHUNK)
    out += body
    out += rest
    glb_path.write_bytes(bytes(out))


def native_to_mesh(part: Part, out_path: str | pathlib.Path, fmt: str, *, pipeline: str = "libtess2") -> dict:
    """Write ``part`` to a binary STL / welded OBJ (``fmt``: ``"stl"`` | ``"obj"``) via adacpp's
    NGEOM-record tessellate+emit core. Output is metres regardless of the model units.
    Returns the audit dict."""
    if not native_mesh_writers_available():
        raise NativeExportUnsupported("adacpp with stream_ngeom_to_mesh is not importable")
    import adacpp

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    records, walk = collect_ngeom_records(part)
    stats = adacpp.cad.stream_ngeom_to_mesh(
        records, str(out_path), fmt, pipeline=pipeline, unit_scale=_unit_scale(part), **_stream_tess_params()
    )
    stats.update(walk)
    _check_mesh_emit_stats(stats, fmt.upper())
    logger.info("native NGEOM->%s: %s", fmt.upper(), stats)
    return stats


def native_to_ifc(part: Part, out_path: str | pathlib.Path, schema: str = "IFC4X3_ADD2") -> dict:
    """Write ``part`` to IFC via adacpp's NGEOM-record emitter. Returns the audit dict."""
    if not native_ngeom_writers_available():
        raise NativeExportUnsupported("adacpp with stream_ngeom_to_ifc is not importable")
    import adacpp

    out_path = pathlib.Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    records, walk = collect_ngeom_records(part)
    stats = adacpp.cad.stream_ngeom_to_ifc(records, str(out_path), schema=schema, unit_scale=_unit_scale(part))
    stats.update(walk)
    if stats.get("solids_skipped"):
        raise NativeExportUnsupported(
            f"native IFC emit skipped {stats['solids_skipped']} solid(s): {stats.get('drop_reasons')}"
        )
    logger.info("native NGEOM->IFC: %s", stats)
    return stats
