"""Extract polyline boundaries of cut surfaces created by negative-volume booleans on a beam."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING

from OCC.Core.BRepAdaptor import BRepAdaptor_Curve, BRepAdaptor_Surface
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut
from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace
from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeHalfSpace
from OCC.Core.BRepTools import BRepTools_WireExplorer, breptools
from OCC.Core.GCPnts import GCPnts_UniformDeflection
from OCC.Core.GeomAbs import (
    GeomAbs_Cone,
    GeomAbs_Cylinder,
    GeomAbs_Line,
    GeomAbs_Plane,
    GeomAbs_Sphere,
)
from OCC.Core.gp import gp_Dir, gp_Pln, gp_Pnt
from OCC.Core.TopoDS import TopoDS_Face, TopoDS_Shape
from OCC.Extend.TopologyUtils import TopologyExplorer

from ada.config import logger
from ada.geom.direction import Direction
from ada.geom.points import Point

if TYPE_CHECKING:
    from ada.api.beams import Beam


@dataclass
class CutEdge:
    """A single edge of a cut surface's boundary, classified by curve type.

    For straight edges (``edge_type == "Line"``), ``points`` has exactly two
    entries (start and end). For curved edges, ``points`` is a polyline
    discretization following the underlying curve.
    """

    edge_type: str
    points: list[Point]


@dataclass
class CutSurface:
    """A face on the cut beam whose surface originates from a negative-volume cutter.

    ``outer_edges`` lists the boundary edges in traversal order, each labelled
    with its curve type so callers can distinguish straight runs from arcs.
    ``outer_polyline`` is the same boundary flattened into a single list of
    points (consecutive duplicates removed).
    """

    surface_type: str
    outer_edges: list[CutEdge]
    outer_polyline: list[Point]
    inner_polylines: list[list[Point]]
    sample_normal: Direction


_SURFACE_TYPE_NAMES = {
    GeomAbs_Plane: "Plane",
    GeomAbs_Cylinder: "Cylinder",
    GeomAbs_Cone: "Cone",
    GeomAbs_Sphere: "Sphere",
}


_CURVE_TYPE_NAMES = {
    0: "Line",
    1: "Circle",
    2: "Ellipse",
    3: "Hyperbola",
    4: "Parabola",
    5: "BezierCurve",
    6: "BSplineCurve",
    7: "OffsetCurve",
}


def _build_halfspace_occ(hs) -> TopoDS_Shape:
    """Build an OCC half-space solid for a BoolHalfSpace primitive."""
    origin = hs.poly.origin
    normal = hs.poly.normal
    gp_origin = gp_Pnt(float(origin[0]), float(origin[1]), float(origin[2]))
    gp_normal = gp_Dir(float(normal[0]), float(normal[1]), float(normal[2]))
    pln = gp_Pln(gp_origin, gp_normal)
    face = BRepBuilderAPI_MakeFace(pln).Face()

    offset = -1.0 if hs.flip else 1.0
    ref = gp_Pnt(
        float(origin[0]) + float(normal[0]) * offset,
        float(origin[1]) + float(normal[1]) * offset,
        float(origin[2]) + float(normal[2]) * offset,
    )
    return BRepPrimAPI_MakeHalfSpace(face, ref).Solid()


def _cutter_to_occ(boolean) -> TopoDS_Shape | None:
    from ada.api.primitives.bool_half_space import BoolHalfSpace

    prim = boolean.primitive if hasattr(boolean, "primitive") else boolean
    if isinstance(prim, BoolHalfSpace):
        return _build_halfspace_occ(prim)
    if hasattr(prim, "solid_occ"):
        try:
            return prim.solid_occ()
        except Exception as ex:
            logger.warning(f"Failed to build OCC for cutter {prim}: {ex}")
            return None
    return None


def _build_uncut_solid(beam: Beam) -> TopoDS_Shape:
    from ada.api.beams import geom_beams as geo_conv
    from ada.occ.geom import geom_to_occ_geom

    geom = geo_conv.straight_beam_to_geom(beam)
    geom.bool_operations = []
    return geom_to_occ_geom(geom)


def _surface_type_name(face: TopoDS_Face) -> str:
    surf = BRepAdaptor_Surface(face, True)
    return _SURFACE_TYPE_NAMES.get(surf.GetType(), "Other")


def _face_normal(face: TopoDS_Face) -> Direction:
    surf = BRepAdaptor_Surface(face, True)
    if surf.GetType() == GeomAbs_Plane:
        n = surf.Plane().Axis().Direction()
        d = Direction(n.X(), n.Y(), n.Z())
    else:
        u_mid = 0.5 * (surf.FirstUParameter() + surf.LastUParameter())
        v_mid = 0.5 * (surf.FirstVParameter() + surf.LastVParameter())
        from OCC.Core.gp import gp_Pnt, gp_Vec

        p = gp_Pnt()
        du = gp_Vec()
        dv = gp_Vec()
        surf.D1(u_mid, v_mid, p, du, dv)
        n = du.Crossed(dv)
        if n.Magnitude() < 1e-12:
            return Direction(0.0, 0.0, 1.0)
        n.Normalize()
        d = Direction(n.X(), n.Y(), n.Z())

    if face.Orientation() == 1:  # TopAbs_REVERSED == 1
        d = Direction(-d[0], -d[1], -d[2])
    return d


def _edge_curve_type(edge) -> str:
    curve_adapt = BRepAdaptor_Curve(edge)
    return _CURVE_TYPE_NAMES.get(curve_adapt.GetType(), "Other")


def _edge_to_points(edge, deflection: float) -> list[Point]:
    curve_adapt = BRepAdaptor_Curve(edge)
    if curve_adapt.GetType() == GeomAbs_Line:
        u0 = curve_adapt.FirstParameter()
        u1 = curve_adapt.LastParameter()
        p0 = curve_adapt.Value(u0)
        p1 = curve_adapt.Value(u1)
        return [Point(p0.X(), p0.Y(), p0.Z()), Point(p1.X(), p1.Y(), p1.Z())]

    sampler = GCPnts_UniformDeflection(curve_adapt, deflection)
    if not sampler.IsDone() or sampler.NbPoints() < 2:
        u0 = curve_adapt.FirstParameter()
        u1 = curve_adapt.LastParameter()
        p0 = curve_adapt.Value(u0)
        p1 = curve_adapt.Value(u1)
        return [Point(p0.X(), p0.Y(), p0.Z()), Point(p1.X(), p1.Y(), p1.Z())]

    pts = []
    for i in range(1, sampler.NbPoints() + 1):
        p = sampler.Value(i)
        pts.append(Point(p.X(), p.Y(), p.Z()))
    return pts


def _point_dist(a: Point, b: Point) -> float:
    return math.sqrt(
        (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2
    )


def _wire_to_edges(
    wire, deflection: float, tol: float
) -> list[CutEdge]:
    explorer = BRepTools_WireExplorer(wire)
    edges: list[CutEdge] = []
    while explorer.More():
        edge = explorer.Current()
        edge_type = _edge_curve_type(edge)
        edge_pts = _edge_to_points(edge, deflection)
        if explorer.Orientation() == 1:  # TopAbs_REVERSED
            edge_pts = list(reversed(edge_pts))
        if edges and _point_dist(edges[-1].points[-1], edge_pts[0]) <= tol:
            edge_pts = edge_pts.copy()
            edge_pts[0] = edges[-1].points[-1]
        if len(edge_pts) >= 2:
            edges.append(CutEdge(edge_type=edge_type, points=edge_pts))
        explorer.Next()
    return edges


def _edges_to_polyline(edges: list[CutEdge], tol: float) -> list[Point]:
    polyline: list[Point] = []
    for e in edges:
        if not polyline:
            polyline.extend(e.points)
            continue
        if _point_dist(polyline[-1], e.points[0]) <= tol:
            polyline.extend(e.points[1:])
        else:
            polyline.extend(e.points)
    if len(polyline) >= 2 and _point_dist(polyline[0], polyline[-1]) <= tol:
        polyline = polyline[:-1]
    return polyline


def _wire_to_polyline(wire, deflection: float, tol: float) -> list[Point]:
    edges = _wire_to_edges(wire, deflection, tol)
    return _edges_to_polyline(edges, tol)


def _face_polylines(
    face: TopoDS_Face, deflection: float, tol: float
) -> tuple[list[CutEdge], list[Point], list[list[Point]]]:
    outer_wire = breptools.OuterWire(face)
    outer_edges = _wire_to_edges(outer_wire, deflection, tol)
    outer = _edges_to_polyline(outer_edges, tol)

    inners: list[list[Point]] = []
    topo = TopologyExplorer(face)
    for w in topo.wires():
        if w.IsSame(outer_wire):
            continue
        inners.append(_wire_to_polyline(w, deflection, tol))
    return outer_edges, outer, inners


def _modified_shapes(algo, shape) -> list:
    modified = algo.Modified(shape)
    try:
        n = modified.Size()
    except Exception:
        n = modified.Extent()
    out = []
    if n > 0:
        try:
            for s in modified:
                out.append(s)
        except TypeError:
            from OCC.Core.TopTools import TopTools_ListIteratorOfListOfShape

            it = TopTools_ListIteratorOfListOfShape(modified)
            while it.More():
                out.append(it.Value())
                it.Next()
    return out


def extract_cut_surfaces(
    beam: Beam,
    deflection: float = 1e-3,
    tol: float = 1e-4,
) -> list[CutSurface]:
    """Return the cut-surface polylines on the beam after applying its negative-volume booleans.

    For each face on the cut solid that originated from a cutter (not from the
    original un-cut beam solid), returns one CutSurface with its outer polyline
    in world coordinates. Curved boundary edges are discretized using
    `deflection` (max sagitta error). Coincident polyline points within `tol`
    are de-duplicated.
    """
    from ada.geom.booleans import BoolOpEnum

    if not beam.booleans:
        return []

    cutters_occ: list[TopoDS_Shape] = []
    for b in beam.booleans:
        bool_op = getattr(b, "bool_op", None)
        if bool_op is not None and bool_op != BoolOpEnum.DIFFERENCE:
            continue
        occ = _cutter_to_occ(b)
        if occ is not None:
            cutters_occ.append(occ)

    if not cutters_occ:
        return []

    original_occ = _build_uncut_solid(beam)

    # Apply cuts sequentially, tracking which faces on the running result are
    # descendants of the original beam. Halfspaces aren't well-behaved under
    # BRepAlgoAPI_Fuse, so we avoid pre-fusing the cutters.
    current = original_occ
    descendants = set(TopologyExplorer(original_occ).faces())
    for cutter_occ in cutters_occ:
        algo = BRepAlgoAPI_Cut(current, cutter_occ)
        algo.Build()
        if not algo.IsDone():
            raise RuntimeError(f"Boolean cut failed for beam {beam.name}")
        next_descendants: set = set()
        for f in descendants:
            modified = _modified_shapes(algo, f)
            if modified:
                for s in modified:
                    next_descendants.add(s)
            elif not algo.IsDeleted(f):
                next_descendants.add(f)
        descendants = next_descendants
        current = algo.Shape()

    result = current

    surfaces: list[CutSurface] = []
    for rf in TopologyExplorer(result).faces():
        if rf in descendants:
            continue
        try:
            outer_edges, outer, inners = _face_polylines(rf, deflection, tol)
        except Exception as ex:
            logger.warning(f"Failed to extract polyline from cut face on {beam.name}: {ex}")
            continue
        if len(outer) < 3:
            continue
        surfaces.append(
            CutSurface(
                surface_type=_surface_type_name(rf),
                outer_edges=outer_edges,
                outer_polyline=outer,
                inner_polylines=inners,
                sample_normal=_face_normal(rf),
            )
        )

    return surfaces
