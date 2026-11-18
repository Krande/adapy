"""Per-object metadata dicts shipped to the viewer's selected-object panel.

The frontend renders typed Properties for clicked beams and plates (section
profile, material elastics, thickness). This module produces the dicts that
populate ``SceneBackend.object_meta`` at file-load time and the responses
returned by ``mesh_info_callback`` at click time.

The serialization pattern mirrors ``ada.fem.formats.code_aster.write.beams_sidecar``
(profile fields walked via ``getattr(..., None)`` then float-coerced) but
broadened to also cover plates and to drop the GENBEAM filter — the viewer
still wants to show what data exists even if the section can't be tessellated.
"""

from __future__ import annotations

import pathlib
from typing import TYPE_CHECKING, Any

from ada.config import logger

if TYPE_CHECKING:
    from ada.api.spatial import Assembly
    from ada.comms.fb_wrap_model_gen import FileObjectDC
    from ada.comms.wsock.scene_model import SceneBackend
    from ada.materials import Material
    from ada.sections import Section


def _maybe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def section_to_dict(section: Section | None) -> dict | None:
    """Flatten a :class:`ada.Section` into a JSON-friendly dict.

    ``None`` in → ``None`` out. Unknown attributes silently become ``None``
    so callers don't need to discriminate I-profile vs tubular vs box at
    serialization time."""
    if section is None:
        return None
    sec_type = getattr(section, "type", None)
    if sec_type is None:
        type_name = None
    elif hasattr(sec_type, "value"):
        type_name = sec_type.value
    else:
        type_name = str(sec_type)
    out = {
        "name": getattr(section, "name", None),
        "type": type_name,
        "h": _maybe_float(getattr(section, "h", None)),
        "w_top": _maybe_float(getattr(section, "w_top", None)),
        "w_btn": _maybe_float(getattr(section, "w_btn", None)),
        "t_w": _maybe_float(getattr(section, "t_w", None)),
        "t_ftop": _maybe_float(getattr(section, "t_ftop", None)),
        "t_fbtn": _maybe_float(getattr(section, "t_fbtn", None)),
        "r": _maybe_float(getattr(section, "r", None)),
        "wt": _maybe_float(getattr(section, "wt", None)),
    }
    # A section given only as an outline (a polygon section) says nothing by name, but an outline
    # that is a rectangle, square-cornered or with four equal round corners, is a box's: fill the
    # box's keys from it where the section does not carry them itself.
    for key, value in outline_box_dims(section).items():
        if out.get(key) is None:
            out[key] = value
    return out


def _seg_xy(p) -> tuple[float, float]:
    return float(p[0]), float(p[1])


def _circle_through(a, b, c) -> tuple[tuple[float, float], float] | None:
    """Centre and radius of the circle through three 2D points, or None when they are in a line."""
    ax, ay = a
    bx, by = b
    cx, cy = c
    d = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-15:
        return None
    a2, b2, c2 = ax * ax + ay * ay, bx * bx + by * by, cx * cx + cy * cy
    ux = (a2 * (by - cy) + b2 * (cy - ay) + c2 * (ay - by)) / d
    uy = (a2 * (cx - bx) + b2 * (ax - cx) + c2 * (bx - ax)) / d
    return (ux, uy), ((ax - ux) ** 2 + (ay - uy) ** 2) ** 0.5


def _arc_circle(seg) -> tuple[tuple[float, float], float] | None:
    mid = getattr(seg, "midpoint", None)
    if mid is not None:
        circle = _circle_through(_seg_xy(seg.p1), _seg_xy(mid), _seg_xy(seg.p2))
        if circle is not None:
            return circle
    center, radius = getattr(seg, "center", None), getattr(seg, "radius", None)
    if center is not None and radius is not None:
        return _seg_xy(center), float(radius)
    return None


def outline_rectangle(curve, tol: float = 1e-6) -> tuple[float, float, float, float, float] | None:
    """``(xmin, xmax, ymin, ymax, r)`` when a closed 2D outline is an axis-aligned rectangle whose
    corners are all square (``r`` 0) or all quarter circles of one radius ``r``; else None.

    Read from the outline's segments: its straight edges must each be horizontal or vertical and lie
    on the bounding box, and each arc must be centred ``r`` in from a distinct corner of it."""
    from ada.api.curves import ArcSegment

    try:
        segments = list(curve.segments)
    except Exception:  # noqa: BLE001 - an outline that cannot be read describes no box
        return None
    if not segments:
        return None
    arcs = [s for s in segments if isinstance(s, ArcSegment)]
    lines = [s for s in segments if not isinstance(s, ArcSegment)]
    if arcs and len(arcs) != 4:
        return None

    pts = [_seg_xy(s.p1) for s in segments] + [_seg_xy(s.p2) for s in segments]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    xmin, xmax, ymin, ymax = min(xs), max(xs), min(ys), max(ys)
    if xmax - xmin <= tol or ymax - ymin <= tol:
        return None

    def on_box_edge(a, b) -> bool:
        (x1, y1), (x2, y2) = a, b
        if abs(x1 - x2) <= tol:
            return abs(x1 - xmin) <= tol or abs(x1 - xmax) <= tol
        if abs(y1 - y2) <= tol:
            return abs(y1 - ymin) <= tol or abs(y1 - ymax) <= tol
        return False

    for line in lines:
        a, b = _seg_xy(line.p1), _seg_xy(line.p2)
        if abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol:
            continue  # a zero-length edge
        if not on_box_edge(a, b):
            return None

    if not arcs:
        corners = {(x, y) for x in (xmin, xmax) for y in (ymin, ymax)}
        found = {c for c in corners if any(abs(c[0] - p[0]) <= tol and abs(c[1] - p[1]) <= tol for p in pts)}
        return (xmin, xmax, ymin, ymax, 0.0) if len(found) == 4 else None

    circles = [_arc_circle(a) for a in arcs]
    if any(c is None for c in circles):
        return None
    r = circles[0][1]
    if r <= tol or any(abs(c[1] - r) > tol for c in circles):
        return None
    seen = set()
    for (cx, cy), _ in circles:
        sx = -1 if abs(cx - (xmin + r)) <= tol else (1 if abs(cx - (xmax - r)) <= tol else 0)
        sy = -1 if abs(cy - (ymin + r)) <= tol else (1 if abs(cy - (ymax - r)) <= tol else 0)
        if sx == 0 or sy == 0:
            return None
        seen.add((sx, sy))
    if len(seen) != 4:
        return None
    return xmin, xmax, ymin, ymax, r


def outline_box_dims(section) -> dict[str, float]:
    """The box keys a polygon section's outlines give: ``h``, ``w_top``, ``w_btn`` and ``r`` (the
    outer corner radius, 0 for square corners) from an outer outline that :func:`outline_rectangle`
    reads as a rectangle, and ``t_w``, ``t_ftop``, ``t_fbtn`` from an inner one inside it. Empty for
    a section with no such outline."""
    outer_curve = getattr(section, "poly_outer", None)
    if outer_curve is None:
        return {}
    outer = outline_rectangle(outer_curve)
    if outer is None:
        return {}
    oxmin, oxmax, oymin, oymax, r = outer
    dims = {"h": oymax - oymin, "w_top": oxmax - oxmin, "w_btn": oxmax - oxmin, "r": r}
    inner_curve = getattr(section, "poly_inner", None)
    inner = outline_rectangle(inner_curve) if inner_curve is not None else None
    if inner is not None:
        ixmin, ixmax, iymin, iymax, _ = inner
        if oxmin < ixmin < ixmax < oxmax and oymin < iymin < iymax < oymax:
            dims["t_w"] = ((ixmin - oxmin) + (oxmax - ixmax)) / 2.0
            dims["t_ftop"] = oymax - iymax
            dims["t_fbtn"] = iymin - oymin
    return {k: float(v) for k, v in dims.items()}


def material_to_dict(material: Material | None) -> dict | None:
    """Flatten an :class:`ada.Material` (+ its ``.model``) into a JSON dict.

    The numeric properties live on ``material.model`` (a CarbonSteel/Metal
    instance). Older Material entries built from FEA-side dumps may have
    ``model=None`` — return name-only in that case rather than crashing the
    metadata lookup."""
    if material is None:
        return None
    out: dict[str, Any] = {"name": getattr(material, "name", None)}
    model = getattr(material, "model", None)
    if model is None:
        return out
    out.update(
        {
            "E": _maybe_float(getattr(model, "E", None)),
            "rho": _maybe_float(getattr(model, "rho", None)),
            "sig_y": _maybe_float(getattr(model, "sig_y", None)),
            "sig_u": _maybe_float(getattr(model, "sig_u", None)),
            "v": _maybe_float(getattr(model, "v", None)),
        }
    )
    return out


def beam_axis(beam) -> dict | None:
    """Where a beam lies: its two end points and its up direction, in the model's coordinates.

    ``{"n1": [x, y, z], "n2": [x, y, z], "up": [x, y, z]}``, or None for something that does not say (a
    FEM line element's section has no axis of its own here). With the section this is all of a straight
    member: enough for a viewer to tell which end of one member meets another, and for a member to be
    made again from its metadata."""
    try:
        n1, n2, up = beam.n1.p, beam.n2.p, beam.up
        return {
            "n1": [float(v) for v in n1],
            "n2": [float(v) for v in n2],
            "up": [float(v) for v in up],
        }
    except Exception:  # noqa: BLE001 - metadata never fails a scene for what it cannot say
        return None


def beam_metadata(
    name: str,
    section: Section | None,
    material: Material | None,
    beam=None,
) -> dict:
    """``beam``, when given, adds ``axis`` (:func:`beam_axis`): its end points and up direction."""
    meta = {
        "type": "Beam",
        "name": name,
        "section": section_to_dict(section),
        "material": material_to_dict(material),
    }
    axis = beam_axis(beam) if beam is not None else None
    if axis is not None:
        meta["axis"] = axis
    return meta


def curved_plate_metadata(
    name: str,
    thickness: float | None,
    material: Material | None,
) -> dict:
    """Info-panel metadata for a ``PlateCurved``. Mirrors :func:`plate_metadata`
    (thickness + material) so a curved plate surfaces the same properties a flat
    plate does; only the ``type`` discriminator differs so the panel can label
    it ``Curved Plate``."""
    return {
        "type": "PlateCurved",
        "name": name,
        "thickness": _maybe_float(thickness),
        "material": material_to_dict(material),
    }


def plate_metadata(
    name: str,
    thickness: float | None,
    material: Material | None,
) -> dict:
    return {
        "type": "Plate",
        "name": name,
        "thickness": _maybe_float(thickness),
        "material": material_to_dict(material),
    }


def _register_beam(scene: SceneBackend, file_name: str, beam) -> None:
    name = getattr(beam, "name", None)
    if not name:
        return
    file_meta = scene.object_meta.setdefault(file_name, {})
    file_meta[name] = beam_metadata(
        name=name,
        section=getattr(beam, "section", None),
        material=getattr(beam, "material", None),
        beam=beam,
    )


def _register_plate(scene: SceneBackend, file_name: str, plate) -> None:
    name = getattr(plate, "name", None)
    if not name:
        return
    file_meta = scene.object_meta.setdefault(file_name, {})
    file_meta[name] = plate_metadata(
        name=name,
        thickness=getattr(plate, "t", None),
        material=getattr(plate, "material", None),
    )


def _register_curved_plate(scene: SceneBackend, file_name: str, plate) -> None:
    name = getattr(plate, "name", None)
    if not name:
        return
    file_meta = scene.object_meta.setdefault(file_name, {})
    file_meta[name] = curved_plate_metadata(
        name=name,
        thickness=getattr(plate, "t", None),
        material=getattr(plate, "material", None),
    )


def build_object_meta_from_assembly(
    scene: SceneBackend,
    file_name: str,
    assembly: Assembly,
    *,
    cad_side: bool,
) -> None:
    """Populate ``scene.object_meta[file_name]`` from an in-memory Assembly.

    ``cad_side=True`` for IFC / native CAD loads — walks
    ``assembly.get_all_physical_objects`` and registers Beam/Plate entries.
    ``cad_side=False`` for FEA loads — walks the FEM and registers one
    entry per element with the section / material data from its FemSection.

    The CAD↔FEA linkage that used to live here is now resolved on the
    frontend via the ``ADA_EXT_data`` extension's ``assembly_guid`` and
    ``parent_object_guid`` fields. This index only carries the
    per-object Properties payload."""
    # Reset this file's slice so reloads don't leave stale entries.
    scene.object_meta[file_name] = {}

    if cad_side:
        _build_cad_index(scene, file_name, assembly)
    else:
        _build_fea_index(scene, file_name, assembly)


def _build_cad_index(scene: SceneBackend, file_name: str, assembly: Assembly) -> None:
    from ada import Beam, Plate, PlateCurved

    # ``get_all_physical_objects`` already walks every subpart, so we
    # don't iterate ``parts`` ourselves — that would visit each Beam /
    # Plate N times for an N-part assembly. PlateCurved is a sibling of
    # Plate (not a subclass), so it needs its own branch or it'd get no
    # Info-panel metadata.
    for obj in assembly.get_all_physical_objects():
        if isinstance(obj, Beam):
            _register_beam(scene, file_name, obj)
        elif isinstance(obj, PlateCurved):
            _register_curved_plate(scene, file_name, obj)
        elif isinstance(obj, Plate):
            _register_plate(scene, file_name, obj)


def _build_fea_index(scene: SceneBackend, file_name: str, assembly: Assembly) -> None:
    """Walk every FemSection and register one entry per element.

    Element naming matches what the GLB exporter emits for FEA scenes
    (``Li{elem.id}`` for line elements, ``EL{elem.id}`` for shell/solid faces
    — see ``ada/fem/results/common.py:292-303`` and
    ``ada/visit/gltf/graph.py``). If the GLB pipeline ever changes these
    prefixes, mirror the change here."""
    fems = [assembly.fem] if assembly.fem is not None else []
    for part in assembly.get_all_parts_in_assembly(True):
        if part.fem is not None and part.fem is not assembly.fem:
            fems.append(part.fem)

    for fem in fems:
        for fem_sec in getattr(fem, "sections", []):
            elset = getattr(fem_sec, "elset", None)
            members = getattr(elset, "members", []) if elset is not None else []
            is_line = fem_sec.section is not None
            is_shell = getattr(fem_sec, "thickness", None) is not None
            for elem in members:
                node_name = _fem_elem_node_name(elem, is_line)
                if node_name is None:
                    continue
                file_meta = scene.object_meta.setdefault(file_name, {})
                if is_line:
                    file_meta[node_name] = beam_metadata(
                        name=node_name,
                        section=fem_sec.section,
                        material=fem_sec.material,
                    )
                elif is_shell:
                    file_meta[node_name] = plate_metadata(
                        name=node_name,
                        thickness=fem_sec.thickness,
                        material=fem_sec.material,
                    )
                # Solid / connector / unknown — skip silently.


def _fem_elem_node_name(elem, is_line: bool) -> str | None:
    elem_id = getattr(elem, "id", None)
    if elem_id is None:
        return None
    prefix = "Li" if is_line else "EL"
    return f"{prefix}{int(elem_id)}"


def populate_for_file_object(scene: SceneBackend, file_object: FileObjectDC) -> None:
    """Lazy-load entry point called after the viewer pulls a file into the
    scene. Reads the file, builds an in-memory Assembly, populates the index.

    Errors are logged and swallowed — a missing metadata index degrades to
    "No metadata available" in the panel; it must not break the click path.
    """
    if file_object is None or file_object.filepath is None:
        return
    file_name = file_object.name
    if file_name in scene.object_meta and scene.object_meta[file_name]:
        return  # already built
    fp = pathlib.Path(file_object.filepath)
    try:
        suffix = fp.suffix.lower()
        if suffix == ".ifc":
            _populate_ifc(scene, file_name, fp)
        elif suffix in {".sif", ".sin", ".fem", ".inp", ".bdf"}:
            _populate_fem(scene, file_name, fp, suffix)
        # GLB-only files have no source Assembly to walk; skip.
    except Exception as exc:  # noqa: BLE001 — metadata is non-critical
        logger.warning(f"object_metadata: failed to populate index for {file_name}: {exc}")


def _populate_ifc(scene: SceneBackend, file_name: str, fp: pathlib.Path) -> None:
    import ada

    assembly = ada.from_ifc(fp)
    build_object_meta_from_assembly(scene, file_name, assembly, cad_side=True)


def _populate_fem(scene: SceneBackend, file_name: str, fp: pathlib.Path, suffix: str) -> None:
    import ada

    assembly = ada.from_fem(fp)
    build_object_meta_from_assembly(scene, file_name, assembly, cad_side=False)


__all__ = [
    "section_to_dict",
    "material_to_dict",
    "beam_metadata",
    "plate_metadata",
    "build_object_meta_from_assembly",
    "populate_for_file_object",
]
