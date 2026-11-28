"""A beam or plate as the MEMBER description adacpp's native IFC writer writes a model member from.

The native writer was handed only geometry -- a solid's NGEOM blob -- and so wrote every member as
an IfcBuildingElementProxy with an approximated solid: no class, no axis, no section, no material,
no property sets, and a half-space cut lowered to a finite box. A reader of that file can draw the
member and cannot rebuild it. This module describes the member the way the ifcopenshell writer
writes it (``write_beams``, ``write_plates``, ``write_sections``, ``create_ifc_openings``,
``write_material``, the property sets), in the record form adacpp's ``stream_ngeom_to_ifc`` takes
as a record's sixth element, so a file written natively reads back -- through ifcopenshell or the
native member scan -- into the same Beam or Plate.

Free of ifcopenshell: the native writer runs where it is not installed.

The record (lengths in model units, the file states the unit)::

    {"ifc_class": "IfcBeam" | "IfcPlate", "guid", "name", "description", "object_type",
     "placement": 16 floats, the world matrix column-major,
     "axis": (p1, p2) | None,
     "profile": {"kind": "curves" | "circle" | "circle_hollow", "name", "outer": curve,
                 "inners": [curve], "radius", "wall_thickness", "properties": {set: {k: v}}},
     "extrusion": {"origin", "axis", "ref", "direction", "depth"},
     "cuts": [{"origin", "normal", "xdir", "agreement"}],        # in the order applied
     "openings": [{"name", "guid", "blob": NGEOM bytes, "placement": 16 floats}],  # other booleans
     "material": {"name", "category", "properties_name", "properties": [(name, value, type)]},
     "psets": {set: {k: v}}, "color": rgba | None}

    curve = {"kind": "polyline" | "indexed", "points": [(x, y[, z])],
             "segments": [("line" | "arc", (1-based indices))] | None}
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.config import logger

if TYPE_CHECKING:
    from ada import Beam, Plate, Section

__all__ = ["member_record", "section_profile_record"]


def _xy(p) -> tuple[float, float]:
    return float(p[0]), float(p[1])


def _closed(points: list) -> list:
    if points and tuple(points[0]) != tuple(points[-1]):
        return [*points, points[0]]
    return points


def _polyline(points, close: bool = False) -> dict:
    pts = [_xy(p) for p in points]
    return {"kind": "polyline", "points": _closed(pts) if close else pts, "segments": None}


def section_profile_record(section: Section) -> dict | None:
    """The profile the ifcopenshell writer writes for ``section`` (``write_sections``), with the
    section's parameter bag (``sections_props.section_param_props``) attached; None for a section
    type that writer has no profile for."""
    from ada.cadit.ifc.sections_props import ADA_SECTION_PSET, section_param_props
    from ada.sections.categories import BaseTypes

    st = section.type
    rec: dict = {"kind": "curves", "name": section.name, "inners": []}
    if st in (BaseTypes.IPROFILE, BaseTypes.TPROFILE, BaseTypes.CHANNEL, BaseTypes.FLATBAR):
        rec["outer"] = _polyline(section.get_section_profile(True).outer_curve.points2d)
    elif st == BaseTypes.ANGULAR:
        rec["outer"] = _polyline(section.get_section_profile(True).outer_curve.points2d, close=True)
    elif st == BaseTypes.BOX:
        prof = section.get_section_profile(True)
        rec["outer"] = _polyline(prof.outer_curve.points2d, close=True)
        rec["inners"] = [_polyline(prof.inner_curve.points2d, close=True)]
    elif st == BaseTypes.POLY:

        def indexed(poly) -> dict:
            pts = [tuple(float(c) for c in p[:3]) for p in poly.seg_global_points]
            segs = [("arc" if len(i) == 3 else "line", tuple(int(k) for k in i)) for i in poly.seg_index]
            return {"kind": "indexed", "points": pts, "segments": segs}

        rec["outer"] = indexed(section.poly_outer)
        if section.poly_inner is not None:
            rec["inners"] = [indexed(section.poly_inner)]
    elif st == BaseTypes.CIRCULAR:
        rec = {"kind": "circle", "name": section.name, "radius": float(section.r)}
    elif st == BaseTypes.TUBULAR:
        rec = {
            "kind": "circle_hollow",
            "name": section.name,
            "radius": float(section.r),
            "wall_thickness": float(section.wt),
        }
    elif st == BaseTypes.GENERAL:
        import numpy as np

        rec = {"kind": "circle", "name": section.name, "radius": float(np.sqrt(section.properties.Ax / np.pi))}
    else:
        return None
    params = section_param_props(section)
    if params:
        rec["properties"] = {ADA_SECTION_PSET: params}
    return rec


def _curve_record(curve) -> dict | None:
    """An ``ada.geom`` profile curve as the ifcopenshell writer writes it (``write.geom.curves``):
    an IndexedPolyCurve with its segments where it has arcs (or the config asks for segments), else
    its points; None for a curve that writer would not write as a polyline (a spline, a conic)."""
    from ada.config import Config
    from ada.geom import curves as geo_cu

    if isinstance(curve, geo_cu.PolyLine):
        return {"kind": "polyline", "points": [_xy(p) for p in curve.points], "segments": None}
    if not isinstance(curve, geo_cu.IndexedPolyCurve):
        return None
    if any(not isinstance(s, (geo_cu.Edge, geo_cu.ArcLine)) for s in curve.segments):
        return None
    has_arcs = any(isinstance(s, geo_cu.ArcLine) for s in curve.segments)
    if has_arcs or getattr(Config(), "ifc_use_index_poly_curve_segments", False):
        unique_pts, seg_idx = curve.get_unique_points_and_segment_indices()
        segs = [
            ("arc" if isinstance(s, geo_cu.ArcLine) else "line", tuple(int(k) for k in idx))
            for s, idx in zip(curve.segments, seg_idx)
        ]
        return {"kind": "indexed", "points": [_xy(p) for p in unique_pts], "segments": segs}
    return {"kind": "indexed", "points": [_xy(p) for p in curve.get_points()], "segments": None}


def _v3(v) -> tuple[float, float, float]:
    return float(v[0]), float(v[1]), float(v[2])


def _extrusion(eas) -> dict:
    pos = eas.position
    return {
        "origin": _v3(pos.location),
        "axis": _v3(pos.axis),
        "ref": _v3(pos.ref_direction),
        "direction": _v3(eas.extruded_direction),
        "depth": float(eas.depth),
    }


def _opening(pen) -> dict | None:
    """A boolean that is not a half-space as the opening the ifcopenshell writer makes of it
    (``write_openings.generate_ifc_opening``): its solid -- here an NGEOM blob, in the primitive's
    own frame -- placed by the primitive's world matrix. None when the solid does not encode."""
    import numpy as np

    from ada.cadit.ngeom.serialize import _Encoder
    from ada.core.guid import create_guid

    prim = pen.primitive
    enc = _Encoder()
    idx = enc.root(prim.solid_geom())
    blob = enc.finish([(idx, str(pen.name))])
    world = prim.world_matrix()
    if world is None:
        world = np.eye(4)
    return {
        "name": str(pen.name),
        "guid": create_guid(),
        "blob": bytes(blob),
        "placement": [float(v) for v in np.asarray(world, dtype=float).flatten(order="F")],
    }


def _cuts(obj) -> tuple[list, list] | None:
    """The object's booleans: half-spaces as clipping cuts (in order) and every other primitive as
    an opening, as the ifcopenshell writer splits them (``create_ifc_openings``). None when an
    opening's solid cannot be encoded (the native writer then writes the object from its solid)."""
    from ada import BoolHalfSpace

    cuts, openings = [], []
    for pen in obj.booleans:
        prim = getattr(pen, "primitive", None)
        if prim is None:
            return None
        if not isinstance(prim, BoolHalfSpace):
            try:
                openings.append(_opening(pen))
            except Exception as exc:  # noqa: BLE001 - not encodable: leave the object to its solid
                logger.debug(f"native IFC writer: opening {pen.name} of {obj.name} not encodable ({exc})")
                return None
            continue
        hs = prim.solid_geom().geometry
        pos = hs.base_surface.position
        cuts.append(
            {
                "origin": _v3(pos.location),
                "normal": _v3(pos.axis),
                "xdir": _v3(pos.ref_direction),
                "agreement": bool(hs.agreement_flag),
            }
        )
    return cuts, openings


def _material(material) -> dict | None:
    if material is None:
        return None
    from ada.cadit.ifc.write.write_material import (
        MATERIAL_CATEGORY,
        MATERIAL_PROPERTIES_NAME,
        material_property_values,
    )

    try:
        props = material_property_values(material)
    except Exception as exc:  # noqa: BLE001 - a material model without these values: name only
        logger.debug(f"native IFC writer: material {material.name!r} written by name only ({exc})")
        props = []
    return {
        "name": material.name,
        "category": MATERIAL_CATEGORY,
        "properties_name": MATERIAL_PROPERTIES_NAME,
        "properties": [(name, value, ifc_type) for name, value, ifc_type in props],
    }


def _psets(metadata) -> dict:
    """The object's property sets, as the ifcopenshell writer splits its metadata; values a flat
    property set cannot hold (a nested dict inside a set) are left out."""
    from ada.cadit.ifc.write.property_sets import element_property_sets

    out = {}
    for set_name, props in element_property_sets(metadata).items():
        flat = {str(k): v for k, v in props.items() if not isinstance(v, dict)}
        if flat:
            out[set_name] = flat
    return out


def _common(obj) -> dict | None:
    import numpy as np

    from ada.base.ifc_types import IFC_ENTITY_KEY

    meta = obj.metadata or {}
    if IFC_ENTITY_KEY in meta or meta.get("segment_ifc_class"):
        return None  # an entity choice the schema decides on: the ifcopenshell writer's business
    split = _cuts(obj)
    if split is None:
        return None
    cuts, openings = split
    world = obj.world_matrix()  # None for the identity
    if world is None:
        world = np.eye(4)
    col = getattr(obj, "color", None)
    return {
        "guid": obj.guid,
        "name": str(obj.name),
        "placement": [float(v) for v in np.asarray(world, dtype=float).flatten(order="F")],
        "cuts": cuts,
        "openings": openings,
        "material": _material(getattr(obj, "material", None)),
        "psets": _psets(meta),
        "color": [float(c) for c in col][:4] if col is not None else None,
    }


def _beam_record(beam: Beam) -> dict | None:
    from ada import Beam
    from ada.geom import solids as geo_so

    if type(beam) is not Beam:
        return None  # swept, revolved, curved, tapered: not a straight extrusion
    geo = beam.solid_geom().geometry
    if not isinstance(geo, geo_so.ExtrudedAreaSolid):
        return None
    profile = section_profile_record(beam.section)
    common = _common(beam)
    if profile is None or common is None:
        return None
    return {
        **common,
        "ifc_class": "IfcBeam",
        "description": beam.section.sec_str,
        "object_type": "Beam",
        "axis": (_v3(beam.n1.p), _v3(beam.n2.p)),
        "profile": profile,
        "extrusion": _extrusion(geo),
    }


def _plate_record(plate: Plate) -> dict | None:
    from ada import Plate
    from ada.api.curves import SplineSegment
    from ada.geom import solids as geo_so
    from ada.geom import surfaces as geo_su

    if type(plate) is not Plate:
        return None
    if any(isinstance(s, SplineSegment) for s in plate.poly.segments3d):
        return None  # written as an analytic B-rep by the ifcopenshell writer
    geo = plate.solid_geom().geometry
    if not isinstance(geo, geo_so.ExtrudedAreaSolid) or not isinstance(geo.swept_area, geo_su.ArbitraryProfileDef):
        return None
    outer = _curve_record(geo.swept_area.outer_curve)
    inners = [_curve_record(c) for c in geo.swept_area.inner_curves]
    common = _common(plate)
    if outer is None or any(c is None for c in inners) or common is None:
        return None
    return {
        **common,
        "ifc_class": "IfcPlate",
        "description": plate.name,
        "object_type": None,
        "axis": None,
        "profile": {"kind": "curves", "name": geo.swept_area.profile_name, "outer": outer, "inners": inners},
        "extrusion": _extrusion(geo),
    }


def member_record(obj) -> dict | None:
    """The member description of a straight ``Beam`` or a flat ``Plate``, or None for anything the
    native writer should write from its solid instead."""
    from ada import Beam, Plate

    try:
        if isinstance(obj, Beam):
            return _beam_record(obj)
        if isinstance(obj, Plate):
            return _plate_record(obj)
    except Exception as exc:  # noqa: BLE001 - a member this cannot describe is still written, as a solid
        logger.debug(f"native IFC writer: {obj.name} ({type(obj).__name__}) not described as a member: {exc}")
    return None
