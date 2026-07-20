from __future__ import annotations

from typing import TYPE_CHECKING

import ifcopenshell

from ada import Plate
from ada.config import logger
from ada.geom import solids as geo_so
from ada.geom import surfaces as geo_su

from .geom.geom_reader import get_product_definitions
from .read_materials import read_material
from .reader_utils import get_associated_material

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore


# Default thickness for curved plates whose IFC carries only a surface (no
# layer-set / thickness). Used purely for the flat-plate render fallback.
_CURVED_PLATE_DEFAULT_T = 0.01


def _is_extruded_arbitrary(geometry) -> bool:
    return isinstance(geometry, geo_so.ExtrudedAreaSolid) and isinstance(
        getattr(geometry, "swept_area", None), geo_su.ArbitraryProfileDef
    )


def _read_plate_material(ifc_elem, name, ifc_store: IfcStore):
    mat = None
    if ifc_store.assembly is not None:
        mat = ifc_store.assembly.get_by_name(name)
    if mat is None:
        mat = read_material(get_associated_material(ifc_elem), ifc_store)
    return mat


def _edge_loop_points(advanced_face: geo_su.AdvancedFace) -> list[tuple[float, float, float]]:
    """Perimeter (vertex) points of the face's outer bound, for the flat fallback."""
    if not advanced_face.bounds:
        return []
    loop = advanced_face.bounds[0].bound
    edge_list = getattr(loop, "edge_list", None) or []
    return [tuple(float(c) for c in edge.start) for edge in edge_list]


def _import_curved_plate(ifc_elem, name, advanced_face: geo_su.AdvancedFace, ifc_store: IfcStore):
    """Import an ``IfcAdvancedFace`` plate as a :class:`PlateCurved`.

    Mirrors the gxml curved-plate path: keep the B-spline surface as the
    geometry and attach the outer-loop endpoints as ``_flat_fallback_pts`` so the
    tessellator degrades to a flat plate if the trimmed B-spline can't be meshed.
    """
    from ada import PlateCurved
    from ada.geom import Geometry

    from .read_color import get_product_color

    color = get_product_color(ifc_elem, ifc_store.f)
    pc = PlateCurved(
        name,
        Geometry(ifc_elem.GlobalId, advanced_face, color),
        t=_CURVED_PLATE_DEFAULT_T,
        mat=_read_plate_material(ifc_elem, name, ifc_store),
        guid=ifc_elem.GlobalId,
        ifc_store=ifc_store,
        units=ifc_store.assembly.units,
        color=color,
    )
    fallback_pts = _edge_loop_points(advanced_face)
    if fallback_pts:
        pc._flat_fallback_pts = fallback_pts
    return pc


def _arc_midpoint(circle, p_start, p_end, same_sense: bool) -> tuple[float, float, float]:
    """The point on the arc of ``circle`` halfway between the two trim points (positive direction)."""
    import math

    import numpy as np

    center = np.asarray(circle.position.location, dtype=float)[:3]
    axis = np.asarray(circle.position.axis, dtype=float)[:3]
    axis = axis / np.linalg.norm(axis)
    if not same_sense:
        axis = -axis
    xd = np.asarray(circle.position.ref_direction, dtype=float)[:3]
    xd = xd - axis * float(xd @ axis)
    xd = xd / np.linalg.norm(xd)
    yd = np.cross(axis, xd)
    r = float(circle.radius)

    def _ang(p):
        d = np.asarray(p, dtype=float)[:3] - center
        return math.atan2(float(d @ yd), float(d @ xd))

    a0, a1 = _ang(p_start), _ang(p_end)
    while a1 <= a0:
        a1 += 2.0 * math.pi
    am = 0.5 * (a0 + a1)
    m = center + r * math.cos(am) * xd + r * math.sin(am) * yd
    return (float(m[0]), float(m[1]), float(m[2]))


def _plate_from_extruded_brep(ifc_elem, name, shell: geo_su.ClosedShell, ifc_store: IfcStore) -> Plate | None:
    """Reconstruct the parametric ``Plate`` from the analytic B-rep the writer emits for a
    spline-boundary plate (``extruded_loop_to_shell``): planar caps + planar/cylindrical sides + one
    or more B-spline side faces whose v-direction IS the extrusion vector. Returns None for any shell
    that doesn't match that shape — the caller then falls back to a generic geometry Shape."""
    import numpy as np

    from ada.api.curves import ArcSegment, CurvePoly2d, LineSegment, SplineSegment
    from ada.geom import curves as geo_cu

    spline_faces = [fc for fc in shell.cfs_faces if isinstance(fc.face_surface, geo_su.BSplineSurfaceWithKnots)]
    if not spline_faces:
        return None
    grid = spline_faces[0].face_surface.control_points_list
    if len(grid[0]) != 2:  # the writer's surface is degree-1 in v: exactly two control columns
        return None
    dvec = np.asarray(grid[0][1], dtype=float)[:3] - np.asarray(grid[0][0], dtype=float)[:3]
    depth = float(np.linalg.norm(dvec))
    if depth < 1e-9:
        return None
    ez = dvec / depth

    def _plane_axis(fc):
        a = np.asarray(fc.face_surface.position.axis, dtype=float)[:3]
        return a / np.linalg.norm(a)

    caps = [fc for fc in shell.cfs_faces if isinstance(fc.face_surface, geo_su.Plane)]
    bottom = next((fc for fc in caps if float(_plane_axis(fc) @ ez) < -0.999), None)
    if bottom is None or not bottom.bounds:
        return None
    loop = bottom.bounds[0].bound
    edge_list = getattr(loop, "edge_list", None)
    if not edge_list:
        return None

    segments = []
    for oe in edge_list:
        ec = oe.edge_element
        geom = getattr(ec, "edge_geometry", None)
        p_start, p_end = (ec.start, ec.end) if oe.orientation else (ec.end, ec.start)
        if isinstance(geom, geo_cu.BSplineCurveWithKnots):
            curve = geom if oe.orientation else _reversed_bspline_curve(geom)
            segments.append(SplineSegment(p_start, p_end, curve=curve))
        elif isinstance(geom, geo_cu.Circle):
            mid = _arc_midpoint(geom, p_start, p_end, getattr(ec, "same_sense", True))
            segments.append(ArcSegment(p_start, p_end, midpoint=mid))
        elif isinstance(geom, (geo_cu.Line, geo_cu.PolyLine)) or geom is None:
            segments.append(LineSegment(p_start, p_end))
        else:
            return None
    # contiguity sanity: each segment must start where the previous one ended
    for i, seg in enumerate(segments):
        nxt = segments[(i + 1) % len(segments)]
        if float(np.linalg.norm(np.asarray(seg.p2, dtype=float) - np.asarray(nxt.p1, dtype=float))) > 1e-6:
            return None

    try:
        poly = CurvePoly2d.from_segments(segments)
        if float(np.asarray(poly.normal, dtype=float) @ ez) < 0.0:
            poly = CurvePoly2d.from_segments(segments, flip_n=True)
    except Exception as exc:  # noqa: BLE001 - fall back to the generic shape import
        logger.debug(f"plate {name}: from_segments reconstruction failed ({exc})")
        return None

    return Plate(
        name,
        poly,
        depth,
        mat=_read_plate_material(ifc_elem, name, ifc_store),
        guid=ifc_elem.GlobalId,
        ifc_store=ifc_store,
        units=ifc_store.assembly.units,
    )


def _reversed_bspline_curve(c):
    from ada.geom.primitive_brep import _reversed_bspline

    return _reversed_bspline(c)


def import_ifc_plate(ifc_elem: ifcopenshell.entity_instance, name, ifc_store: IfcStore):
    logger.info(f"importing {name}")
    geometries = get_product_definitions(ifc_elem)

    # A curved (B-spline) plate surface -> PlateCurved (with flat-plate fallback).
    if len(geometries) == 1 and isinstance(geometries[0], geo_su.AdvancedFace):
        return _import_curved_plate(ifc_elem, name, geometries[0], ifc_store)

    # A spline-boundary plate is written as an analytic IfcAdvancedBrep (ClosedShell); rebuild the
    # parametric Plate from its bottom cap loop.
    if len(geometries) == 1 and isinstance(geometries[0], geo_su.ClosedShell):
        pl = _plate_from_extruded_brep(ifc_elem, name, geometries[0], ifc_store)
        if pl is not None:
            return pl

    # Only an extruded arbitrary profile maps to a parametric Plate. Anything else
    # (e.g. another BREP form) is imported as a generic geometry-backed Shape so it
    # still renders and round-trips.
    if len(geometries) != 1 or not _is_extruded_arbitrary(geometries[0]):
        from .read_shapes import import_ifc_shape

        return import_ifc_shape(ifc_elem, name, ifc_store, force_geom=True)

    body: geo_so.ExtrudedAreaSolid = geometries[0]
    points2d = body.swept_area.outer_curve.to_points2d()
    ifc_mat = get_associated_material(ifc_elem)

    mat = None
    if ifc_store.assembly is not None:
        mat = ifc_store.assembly.get_by_name(name)

    if mat is None:
        mat = read_material(ifc_mat, ifc_store)

    return Plate(
        name,
        points2d,
        body.depth,
        origin=body.position.location,
        xdir=body.position.ref_direction,
        normal=body.position.axis,
        mat=mat,
        guid=ifc_elem.GlobalId,
        ifc_store=ifc_store,
        units=ifc_store.assembly.units,
    )
