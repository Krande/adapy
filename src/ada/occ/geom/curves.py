from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
from OCC.Core.GC import GC_MakeArcOfCircle
from OCC.Core.gp import gp_Pnt
from OCC.Core.TopoDS import TopoDS_Edge, TopoDS_Wire

from ada.geom.curves import ArcLine, IndexedPolyCurve, Line
from ada.geom.surfaces import PolyLoop


def segments_to_edges(segments: list[Line | ArcLine]) -> list[TopoDS_Edge]:
    edges = []
    for seg in segments:
        if isinstance(seg, ArcLine):
            a_arc_of_circle = GC_MakeArcOfCircle(gp_Pnt(*seg.start), gp_Pnt(*seg.center), gp_Pnt(*seg.end))
            a_edge2 = BRepBuilderAPI_MakeEdge(a_arc_of_circle.Value()).Edge()
            edges.append(a_edge2)
        else:
            edge = BRepBuilderAPI_MakeEdge(gp_Pnt(*seg.start), gp_Pnt(*seg.end)).Edge()
            edges.append(edge)

    return edges


def segments_to_wire(segments: list[Line | ArcLine]) -> TopoDS_Wire:
    wire = BRepBuilderAPI_MakeWire()
    for seg in segments_to_edges(segments):
        wire.Add(seg)
    wire.Build()
    return wire.Wire()


def make_wire_from_indexed_poly_curve_geom(curve: IndexedPolyCurve) -> TopoDS_Wire:
    return segments_to_wire(curve.segments)


def make_wire_from_poly_loop(poly_loop: PolyLoop) -> TopoDS_Wire:
    wire = BRepBuilderAPI_MakeWire()
    loop_plus_first = poly_loop.polygon + [poly_loop.polygon[0]]
    for p1, p2 in zip(loop_plus_first[:-1], loop_plus_first[1:]):
        wire.Add(BRepBuilderAPI_MakeEdge(gp_Pnt(*p1), gp_Pnt(*p2)).Edge())
    wire.Build()
    return wire.Wire()
