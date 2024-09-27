from __future__ import annotations

import ifcopenshell

from ada.cadit.ifc.write.geom.placement import ifc_placement_from_axis3d, vector
from ada.cadit.ifc.write.geom.points import cpt, vrtx
from ada.geom import curves as geo_cu


def indexed_poly_curve(ipc: geo_cu.IndexedPolyCurve, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts an IndexedPolyCurve to an IFC representation"""
    unique_pts, segment_indices = ipc.get_points_and_segment_indices()

    points = unique_pts.tolist()
    if len(points[0]) == 2:
        list_type = "IfcCartesianPointList2D"
    else:
        list_type = "IfcCartesianPointList3D"

    ifc_point_list = f.create_entity(list_type, points)
    s = [
        f.create_entity("IfcArcIndex", i) if len(i) == 3 else f.create_entity("IfcLineIndex", i)
        for i in segment_indices
    ]

    return f.create_entity("IfcIndexedPolyCurve", ifc_point_list, s, False)


def circle_curve(circle: geo_cu.Circle, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a Circle to an IFC representation"""
    axis3d = ifc_placement_from_axis3d(circle.position, f)
    return f.create_entity("IfcCircle", axis3d, circle.radius)


def create_edge(e: geo_cu.Edge, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts an Edge to an IFC representation"""
    return f.create_entity("IfcEdge", EdgeStart=vrtx(f, e.start), EdgeEnd=vrtx(f, e.end))


def rational_b_spline_curve_with_knots(
    rbs: geo_cu.RationalBSplineCurveWithKnots, f: ifcopenshell.file
) -> ifcopenshell.entity_instance:
    """Converts a RationalBSplineCurveWithKnots to an IFC representation"""
    control_points = [cpt(f, x) for x in rbs.control_points_list]

    return f.create_entity(
        "IfcRationalBSplineCurveWithKnots",
        Degree=rbs.degree,
        ControlPointsList=control_points,
        CurveForm=rbs.curve_form.value,
        ClosedCurve=rbs.closed_curve,
        SelfIntersect=rbs.self_intersect,
        Knots=rbs.knots,
        KnotMultiplicities=rbs.knot_multiplicities,
        KnotSpec=rbs.knot_spec.value,
        WeightsData=rbs.weights_data,
    )


def b_spline_curve_with_knots(bs: geo_cu.BSplineCurveWithKnots, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a BSplineCurveWithKnots to an IFC representation"""
    control_points = [cpt(f, x) for x in bs.control_points_list]

    return f.create_entity(
        "IfcBSplineCurveWithKnots",
        Degree=bs.degree,
        ControlPointsList=control_points,
        CurveForm=bs.curve_form.value,
        ClosedCurve=bs.closed_curve,
        SelfIntersect=bs.self_intersect,
        Knots=bs.knots,
        KnotMultiplicities=bs.knot_multiplicities,
        KnotSpec=bs.knot_spec.value,
    )


def create_edge_curve(ec: geo_cu.EdgeCurve, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts an EdgeCurve to an IFC representation"""
    if isinstance(ec.edge_geometry, geo_cu.RationalBSplineCurveWithKnots):
        edge_geometry = rational_b_spline_curve_with_knots(ec.edge_geometry, f)
    elif isinstance(ec.edge_geometry, geo_cu.BSplineCurveWithKnots):
        edge_geometry = b_spline_curve_with_knots(ec.edge_geometry, f)
    elif isinstance(ec.edge_geometry, geo_cu.Ellipse):
        edge_geometry = create_ellipse(ec.edge_geometry, f)
    elif isinstance(ec.edge_geometry, geo_cu.Circle):
        edge_geometry = circle_curve(ec.edge_geometry, f)
    elif isinstance(ec.edge_geometry, geo_cu.Line):
        edge_geometry = create_line(ec.edge_geometry, f)
    else:
        raise NotImplementedError(f"Unsupported edge geometry type: {type(ec.edge_geometry)}")

    return f.create_entity(
        "IfcEdgeCurve",
        EdgeStart=vrtx(f, ec.start),
        EdgeEnd=vrtx(f, ec.end),
        EdgeGeometry=edge_geometry,
        SameSense=ec.same_sense,
    )


def create_ellipse(ellipse: geo_cu.Ellipse, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts an Ellipse to an IFC representation"""
    axis3d = ifc_placement_from_axis3d(ellipse.position, f)
    return f.create_entity("IfcEllipse", axis3d, ellipse.semi_axis1, ellipse.semi_axis2)


def create_line(line: geo_cu.Line, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a Line to an IFC representation"""
    return f.create_entity("IfcLine", Pnt=cpt(f, line.pnt), Dir=vector(line.dir, f))


def oriented_edge(oe: geo_cu.OrientedEdge, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts an OrientedEdge to an IFC representation"""
    if isinstance(oe.edge_element, geo_cu.EdgeCurve):
        edge_element = create_edge_curve(oe.edge_element, f)
    elif isinstance(oe.edge_element, geo_cu.Edge):
        edge_element = create_edge(oe.edge_element, f)
    else:
        raise NotImplementedError(f"Unsupported edge element type: {type(oe.edge_element)}")

    return f.create_entity(
        "IfcOrientedEdge",
        EdgeStart=None,
        EdgeEnd=None,
        EdgeElement=edge_element,
        Orientation=oe.orientation,
    )


def edge_loop(el: geo_cu.EdgeLoop, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts an EdgeLoop to an IFC representation"""
    edges = [oriented_edge(e, f) for e in el.edge_list]
    return f.create_entity("IfcEdgeLoop", EdgeList=edges)
