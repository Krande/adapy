"""Pure pythonocc-core implementation of cut-surface extraction.

This is the OccBackend's private implementation of the ``cut_surfaces`` /
``make_halfspace`` CAD-backend verbs. It operates exclusively on raw OCC
``TopoDS_Shape`` handles and returns backend-neutral plain data
(strings/floats/tuples) — no ``ada.geom`` or kernel types leak across the
boundary. The adacpp backend has an independent native implementation of the
same verbs; neither relies on the other.

Return contract for ``occ_cut_surfaces`` — a list of ``SurfData`` tuples::

    (surface_type: str,
     sample_normal: (x, y, z),
     outer_edges: [(edge_type: str, points: [(x, y, z), ...]), ...],
     outer_polyline: [(x, y, z), ...],
     inner_polylines: [[(x, y, z), ...], ...])
"""

from __future__ import annotations

import math

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

_XYZ = tuple  # (x, y, z)

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


def occ_make_halfspace(origin, normal, flip: bool) -> TopoDS_Shape:
    """Build an OCC half-space solid: an infinite half-space bounded by the
    plane (origin, normal). ``flip`` selects which side is solid."""
    gp_origin = gp_Pnt(float(origin[0]), float(origin[1]), float(origin[2]))
    gp_normal = gp_Dir(float(normal[0]), float(normal[1]), float(normal[2]))
    pln = gp_Pln(gp_origin, gp_normal)
    face = BRepBuilderAPI_MakeFace(pln).Face()

    offset = -1.0 if flip else 1.0
    ref = gp_Pnt(
        float(origin[0]) + float(normal[0]) * offset,
        float(origin[1]) + float(normal[1]) * offset,
        float(origin[2]) + float(normal[2]) * offset,
    )
    return BRepPrimAPI_MakeHalfSpace(face, ref).Solid()


def _surface_type_name(face: TopoDS_Face) -> str:
    surf = BRepAdaptor_Surface(face, True)
    return _SURFACE_TYPE_NAMES.get(surf.GetType(), "Other")


def _face_normal(face: TopoDS_Face):
    surf = BRepAdaptor_Surface(face, True)
    if surf.GetType() == GeomAbs_Plane:
        n = surf.Plane().Axis().Direction()
        d = (n.X(), n.Y(), n.Z())
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
            return (0.0, 0.0, 1.0)
        n.Normalize()
        d = (n.X(), n.Y(), n.Z())

    if face.Orientation() == 1:  # TopAbs_REVERSED == 1
        d = (-d[0], -d[1], -d[2])
    return d


def _edge_curve_type(edge) -> str:
    curve_adapt = BRepAdaptor_Curve(edge)
    return _CURVE_TYPE_NAMES.get(curve_adapt.GetType(), "Other")


def _edge_to_points(edge, deflection: float):
    curve_adapt = BRepAdaptor_Curve(edge)
    if curve_adapt.GetType() == GeomAbs_Line:
        u0 = curve_adapt.FirstParameter()
        u1 = curve_adapt.LastParameter()
        p0 = curve_adapt.Value(u0)
        p1 = curve_adapt.Value(u1)
        return [(p0.X(), p0.Y(), p0.Z()), (p1.X(), p1.Y(), p1.Z())]

    sampler = GCPnts_UniformDeflection(curve_adapt, deflection)
    if not sampler.IsDone() or sampler.NbPoints() < 2:
        u0 = curve_adapt.FirstParameter()
        u1 = curve_adapt.LastParameter()
        p0 = curve_adapt.Value(u0)
        p1 = curve_adapt.Value(u1)
        return [(p0.X(), p0.Y(), p0.Z()), (p1.X(), p1.Y(), p1.Z())]

    pts = []
    for i in range(1, sampler.NbPoints() + 1):
        p = sampler.Value(i)
        pts.append((p.X(), p.Y(), p.Z()))
    return pts


def _point_dist(a, b) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _wire_to_edges(wire, deflection: float, tol: float):
    explorer = BRepTools_WireExplorer(wire)
    edges = []
    while explorer.More():
        edge = explorer.Current()
        edge_type = _edge_curve_type(edge)
        edge_pts = _edge_to_points(edge, deflection)
        if explorer.Orientation() == 1:  # TopAbs_REVERSED
            edge_pts = list(reversed(edge_pts))
        if edges and _point_dist(edges[-1][1][-1], edge_pts[0]) <= tol:
            edge_pts = edge_pts.copy()
            edge_pts[0] = edges[-1][1][-1]
        if len(edge_pts) >= 2:
            edges.append((edge_type, edge_pts))
        explorer.Next()
    return edges


def _edges_to_polyline(edges, tol: float):
    polyline = []
    for _etype, pts in edges:
        if not polyline:
            polyline.extend(pts)
            continue
        if _point_dist(polyline[-1], pts[0]) <= tol:
            polyline.extend(pts[1:])
        else:
            polyline.extend(pts)
    if len(polyline) >= 2 and _point_dist(polyline[0], polyline[-1]) <= tol:
        polyline = polyline[:-1]
    return polyline


def _wire_to_polyline(wire, deflection: float, tol: float):
    return _edges_to_polyline(_wire_to_edges(wire, deflection, tol), tol)


def _face_polylines(face: TopoDS_Face, deflection: float, tol: float):
    outer_wire = breptools.OuterWire(face)
    outer_edges = _wire_to_edges(outer_wire, deflection, tol)
    outer = _edges_to_polyline(outer_edges, tol)

    inners = []
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


def occ_cut_surfaces(solid: TopoDS_Shape, cutters, deflection: float, tol: float):
    """Cut ``solid`` by each cutter in turn (pure OCC ``BRepAlgoAPI_Cut`` with
    history), then return plain-data for every result face that originated from
    a cutter (i.e. is not a descendant of the original solid). See module
    docstring for the return contract."""
    current = solid
    descendants = set(TopologyExplorer(solid).faces())
    for cutter in cutters:
        algo = BRepAlgoAPI_Cut(current, cutter)
        algo.Build()
        if not algo.IsDone():
            raise RuntimeError("Boolean cut failed")
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

    surfaces = []
    for rf in TopologyExplorer(current).faces():
        if rf in descendants:
            continue
        try:
            outer_edges, outer, inners = _face_polylines(rf, deflection, tol)
        except Exception as ex:
            logger.warning(f"Failed to extract polyline from cut face: {ex}")
            continue
        if len(outer) < 3:
            continue
        surfaces.append((_surface_type_name(rf), _face_normal(rf), outer_edges, outer, inners))
    return surfaces
