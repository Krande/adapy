from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
from OCC.Core.GC import GC_MakeArcOfCircle
from OCC.Core.gp import gp_Pnt, gp_Ax2, gp_Dir, gp_Circ
from OCC.Core.TopoDS import TopoDS_Edge, TopoDS_Wire

import ada.geom.curves as geo_cu
from ada.geom.surfaces import PolyLoop


def segments_to_edges(segments: list[geo_cu.Line | geo_cu.ArcLine]) -> list[TopoDS_Edge]:
    edges = []
    for seg in segments:
        if isinstance(seg, geo_cu.ArcLine):
            a_arc_of_circle = GC_MakeArcOfCircle(gp_Pnt(*seg.start), gp_Pnt(*seg.center), gp_Pnt(*seg.end))
            a_edge2 = BRepBuilderAPI_MakeEdge(a_arc_of_circle.Value()).Edge()
            edges.append(a_edge2)
        else:
            edge = BRepBuilderAPI_MakeEdge(gp_Pnt(*seg.start), gp_Pnt(*seg.end)).Edge()
            edges.append(edge)

    return edges


def segments_to_wire(segments: list[geo_cu.Line | geo_cu.ArcLine]) -> TopoDS_Wire:
    wire = BRepBuilderAPI_MakeWire()
    for seg in segments_to_edges(segments):
        wire.Add(seg)
    wire.Build()
    return wire.Wire()


def make_wire_from_indexed_poly_curve_geom(curve: geo_cu.IndexedPolyCurve) -> TopoDS_Wire:
    return segments_to_wire(curve.segments)


def make_wire_from_poly_loop(poly_loop: PolyLoop) -> TopoDS_Wire:
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
