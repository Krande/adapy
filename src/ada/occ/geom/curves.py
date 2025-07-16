from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
from OCC.Core.GC import GC_MakeArcOfCircle
from OCC.Core.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Pnt
from OCC.Core.TopAbs import TopAbs_FORWARD
from OCC.Core.TopoDS import TopoDS_Edge, TopoDS_Wire

from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su
from ada.occ.exceptions import UnableToCreateCurveOCCGeom
from ada.occ.utils import point3d


def make_edge_from_line(geom: geo_cu.Edge | geo_cu.ArcLine) -> TopoDS_Edge:
    if isinstance(geom, geo_cu.ArcLine):
        a_arc_of_circle = GC_MakeArcOfCircle(point3d(geom.start), point3d(geom.midpoint), point3d(geom.end))
        return BRepBuilderAPI_MakeEdge(a_arc_of_circle.Value()).Edge()
    else:
        return BRepBuilderAPI_MakeEdge(point3d(geom.start), point3d(geom.end)).Edge()


def segments_to_edges(
    segments: list[geo_cu.Edge | geo_cu.ArcLine],
) -> list[TopoDS_Edge]:
    return [make_edge_from_line(seg) for seg in segments]


def make_edge_from_edge(edge: geo_cu.Edge) -> TopoDS_Edge:
    occ_edge = BRepBuilderAPI_MakeEdge(point3d(edge.start), point3d(edge.end)).Edge()
    occ_edge.Orientation(TopAbs_FORWARD)

    return occ_edge


def segments_to_wire(segments: list[geo_cu.Edge | geo_cu.ArcLine]) -> TopoDS_Wire:
    wire = BRepBuilderAPI_MakeWire()
    for seg in segments_to_edges(segments):
        wire.Add(seg)
    wire.Build()
    try:
        return wire.Wire()
    except RuntimeError:
        raise UnableToCreateCurveOCCGeom("Segments do not form a closed loop")


def make_wire_from_indexed_poly_curve_geom(curve: geo_cu.IndexedPolyCurve) -> TopoDS_Wire:
    return segments_to_wire(curve.segments)


def make_wire_from_poly_loop(poly_loop: geo_cu.PolyLoop) -> TopoDS_Wire:
    wire = BRepBuilderAPI_MakeWire()
    loop_plus_first = poly_loop.polygon + [poly_loop.polygon[0]]
    for p1, p2 in zip(loop_plus_first[:-1], loop_plus_first[1:]):
        wire.Add(BRepBuilderAPI_MakeEdge(gp_Pnt(*p1), gp_Pnt(*p2)).Edge())
    wire.Build()
    return wire.Wire()


def make_wire_from_circle(circle: geo_cu.Circle) -> TopoDS_Wire:
    circle_origin = gp_Ax2(gp_Pnt(*circle.position.location), gp_Dir(*circle.position.axis))
    circle = gp_Circ(circle_origin, circle.radius)

    circle_edge = BRepBuilderAPI_MakeEdge(circle).Edge()
    wire = BRepBuilderAPI_MakeWire()
    wire.Add(circle_edge)
    wire.Build()
    return wire.Wire()


def make_wire_from_edge_loop(edge_loop: geo_cu.EdgeLoop) -> TopoDS_Wire:
    wire = BRepBuilderAPI_MakeWire()
    for para_edge in edge_loop.edge_list:
        occ_edge = make_edge_from_edge(para_edge)
        wire.Add(occ_edge)
    wire.Build()
    return wire.Wire()


def make_wire_from_curve(outer_curve: geo_cu.CURVE_GEOM_TYPES):
    if isinstance(outer_curve, geo_cu.IndexedPolyCurve):
        return make_wire_from_indexed_poly_curve_geom(outer_curve)
    elif isinstance(outer_curve, geo_cu.Circle):
        return make_wire_from_circle(outer_curve)
    elif isinstance(outer_curve, geo_cu.Edge):
        return segments_to_wire([outer_curve])
    else:
        raise NotImplementedError(f"Unsupported curve type {type(outer_curve)}")


def make_wire_from_face_bound(face_bound: geo_su.FaceBound) -> TopoDS_Wire:
    if isinstance(face_bound.bound, geo_cu.PolyLoop):
        return make_wire_from_poly_loop(face_bound.bound)
    if isinstance(face_bound.bound, geo_cu.EdgeLoop):
        return make_wire_from_edge_loop(face_bound.bound)
    else:
        raise NotImplementedError("Only PolyLoop bounds are supported")
