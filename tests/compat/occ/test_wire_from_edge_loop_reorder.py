"""make_wire_from_edge_loop must tolerate an out-of-order edge list.

Sequential BRepBuilderAPI_MakeWire.Add chains each edge to the previous edge's
free vertex, so an EdgeLoop whose edges are not in connection order fails to build
even though the edges DO form a closed loop — these dropped as "Failed to build
wire from N edges" (152 curved faces on a large real CAD assembly). A ShapeFix_Wire
reorder pass recovers them. Fixtures are pure ada.geom Geometry objects (the
STEP->Geometry parse is not exercised — only the Geometry->OCC build path).
"""

from __future__ import annotations

import ada.geom.curves as geo_cu


def _line_edge(start, end) -> geo_cu.OrientedEdge:
    ec = geo_cu.EdgeCurve(
        start=start,
        end=end,
        edge_geometry=geo_cu.Line(start, [e - s for s, e in zip(start, end)]),
        same_sense=True,
    )
    return geo_cu.OrientedEdge(start=start, end=end, edge_element=ec, orientation=True)


def _is_closed(wire) -> bool:
    return wire is not None and not wire.IsNull() and bool(wire.Closed())


def test_in_order_quad_loop_builds():
    from ada.occ.geom.curves import make_wire_from_edge_loop

    p = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
    loop = geo_cu.EdgeLoop(edge_list=[_line_edge(p[i], p[(i + 1) % 4]) for i in range(4)])
    assert _is_closed(make_wire_from_edge_loop(loop))


def test_out_of_order_quad_loop_is_reordered():
    from ada.occ.geom.curves import make_wire_from_edge_loop

    p = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
    edges = [_line_edge(p[i], p[(i + 1) % 4]) for i in range(4)]
    # shuffle so consecutive edges do NOT connect end->start (0,2,1,3)
    shuffled = [edges[0], edges[2], edges[1], edges[3]]
    loop = geo_cu.EdgeLoop(edge_list=shuffled)
    assert _is_closed(make_wire_from_edge_loop(loop)), "out-of-order loop should be reordered into a closed wire"
