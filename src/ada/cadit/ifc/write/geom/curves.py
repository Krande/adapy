from __future__ import annotations

import ifcopenshell

from ada.cadit.ifc.write.geom.placement import ifc_placement_from_axis3d, vector
from ada.cadit.ifc.write.geom.points import cpt, vrtx
from ada.config import Config, logger
from ada.geom import curves as geo_cu


def indexed_poly_curve_from_points_and_segments(
    points: list[list[float]], f: ifcopenshell.file, segment_indices: list[list[int]] = None
) -> ifcopenshell.entity_instance:
    if len(points[0]) == 2:
        list_type = "IfcCartesianPointList2D"
    else:
        list_type = "IfcCartesianPointList3D"

    ifc_point_list = f.create_entity(list_type, CoordList=points)
    if segment_indices is not None:
        s = [
            f.create_entity("IfcArcIndex", i) if len(i) == 3 else f.create_entity("IfcLineIndex", i)
            for i in segment_indices
        ]

        return f.create_entity("IfcIndexedPolyCurve", Points=ifc_point_list, Segments=s, SelfIntersect=None)
    else:
        return f.create_entity("IfcIndexedPolyCurve", Points=ifc_point_list, Segments=None, SelfIntersect=None)


def indexed_poly_curve(ipc: geo_cu.IndexedPolyCurve, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts an IndexedPolyCurve to an IFC representation"""
    has_arclines = any([isinstance(seg, geo_cu.ArcLine) for seg in ipc.segments])
    use_segments = Config().ifc_use_index_poly_curve_segments
    if use_segments or has_arclines:
        unique_pts, segment_indices = ipc.get_unique_points_and_segment_indices()
        if use_segments is False and has_arclines:
            logger.info("Forcing the use indexed poly curve segments because it contains Arc segments")
        points = unique_pts.tolist()
        return indexed_poly_curve_from_points_and_segments(points, f, segment_indices)
    else:
        points = ipc.get_points()

        return indexed_poly_curve_from_points_and_segments(points, f)


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


def poly_line(pl: geo_cu.PolyLine, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a PolyLine to an IFC representation"""
    return f.create_entity("IfcPolyline", Points=[cpt(f, p) for p in pl.points])


def _edge_geometry_3d(edge_geometry, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    if isinstance(edge_geometry, geo_cu.RationalBSplineCurveWithKnots):
        return rational_b_spline_curve_with_knots(edge_geometry, f)
    elif isinstance(edge_geometry, geo_cu.BSplineCurveWithKnots):
        return b_spline_curve_with_knots(edge_geometry, f)
    elif isinstance(edge_geometry, geo_cu.Ellipse):
        return create_ellipse(edge_geometry, f)
    elif isinstance(edge_geometry, geo_cu.Circle):
        return circle_curve(edge_geometry, f)
    elif isinstance(edge_geometry, geo_cu.Line):
        return create_line(edge_geometry, f)
    elif isinstance(edge_geometry, geo_cu.PolyLine):
        return poly_line(edge_geometry, f)
    elif isinstance(edge_geometry, geo_cu.IndexedPolyCurve):
        return indexed_poly_curve(edge_geometry, f)
    else:
        raise NotImplementedError(f"Unsupported edge geometry type: {type(edge_geometry)}")


def b_spline_curve_2d(pc: geo_cu.Pcurve2dBSpline, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Write a 2D (UV) B-spline curve — the parameter-space p-curve on a surface."""
    pts = [f.create_entity("IfcCartesianPoint", (float(u), float(v))) for u, v in pc.control_points_2d]
    common = dict(
        Degree=pc.degree,
        ControlPointsList=pts,
        CurveForm="UNSPECIFIED",
        ClosedCurve=bool(pc.closed),
        SelfIntersect=False,
        Knots=[float(x) for x in pc.knots],
        KnotMultiplicities=[int(x) for x in pc.knot_multiplicities],
        KnotSpec="UNSPECIFIED",
    )
    if pc.weights:
        return f.create_entity("IfcRationalBSplineCurveWithKnots", WeightsData=[float(x) for x in pc.weights], **common)
    return f.create_entity("IfcBSplineCurveWithKnots", **common)


def create_surface_curve(
    ec: geo_cu.EdgeCurve,
    pcurve: geo_cu.Pcurve2dBSpline,
    basis_surface: ifcopenshell.entity_instance,
    f: ifcopenshell.file,
) -> ifcopenshell.entity_instance:
    """Build an IfcSurfaceCurve carrying both the 3D curve and its UV p-curve.

    Preserving the p-curve is what lets a re-imported B-spline face tessellate
    (OCC needs the 2D parametric curve to trim the surface; the bare 3D curve
    alone yields a degenerate, near-zero-area mesh)."""
    curve_3d = _edge_geometry_3d(ec.edge_geometry, f)
    ifc_pcurve = f.create_entity("IfcPcurve", BasisSurface=basis_surface, ReferenceCurve=b_spline_curve_2d(pcurve, f))
    return f.create_entity(
        "IfcSurfaceCurve",
        Curve3D=curve_3d,
        AssociatedGeometry=[ifc_pcurve],
        MasterRepresentation="CURVE3D",
    )


def create_edge_curve(
    ec: geo_cu.EdgeCurve,
    f: ifcopenshell.file,
    pcurve: geo_cu.Pcurve2dBSpline | None = None,
    basis_surface: ifcopenshell.entity_instance | None = None,
) -> ifcopenshell.entity_instance:
    """Converts an EdgeCurve to an IFC representation.

    When a UV ``pcurve`` and its ``basis_surface`` are supplied, the edge
    geometry is written as an IfcSurfaceCurve so the p-curve survives the
    round-trip; otherwise just the bare 3D curve is written."""
    if pcurve is not None and basis_surface is not None:
        edge_geometry = create_surface_curve(ec, pcurve, basis_surface, f)
    else:
        edge_geometry = _edge_geometry_3d(ec.edge_geometry, f)

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


def _basis_curve(curve: geo_cu.CURVE_GEOM_TYPES, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Write a TrimmedCurve basis curve to its matching IFC curve entity."""
    if isinstance(curve, geo_cu.Line):
        return create_line(curve, f)
    elif isinstance(curve, geo_cu.Circle):
        return circle_curve(curve, f)
    elif isinstance(curve, geo_cu.Ellipse):
        return create_ellipse(curve, f)
    elif isinstance(curve, geo_cu.BSplineCurveWithKnots):
        return b_spline_curve_with_knots(curve, f)
    raise NotImplementedError(f"Unsupported trimmed-curve basis type: {type(curve)}")


def _trim_select(trim, f: ifcopenshell.file) -> tuple:
    """A single IfcTrimmingSelect entry: a Cartesian point or a parameter value."""
    from ada.geom.points import Point

    if isinstance(trim, Point):
        return (cpt(f, trim),)
    return (float(trim),)


def create_trimmed_curve(tc: geo_cu.TrimmedCurve, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a TrimmedCurve to an IfcTrimmedCurve."""
    return f.create_entity(
        "IfcTrimmedCurve",
        BasisCurve=_basis_curve(tc.basis_curve, f),
        Trim1=_trim_select(tc.trim1, f),
        Trim2=_trim_select(tc.trim2, f),
        SenseAgreement=tc.sense_agreement,
        MasterRepresentation=tc.master_representation,
    )


def oriented_edge(
    oe: geo_cu.OrientedEdge,
    f: ifcopenshell.file,
    basis_surface: ifcopenshell.entity_instance | None = None,
) -> ifcopenshell.entity_instance:
    """Converts an OrientedEdge to an IFC representation.

    ``basis_surface`` (the parent face's surface entity) enables writing the
    edge's UV p-curve when present."""
    if isinstance(oe.edge_element, geo_cu.EdgeCurve):
        pcurve = getattr(oe, "pcurve", None)
        edge_element = create_edge_curve(oe.edge_element, f, pcurve=pcurve, basis_surface=basis_surface)
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


def edge_loop(
    el: geo_cu.EdgeLoop,
    f: ifcopenshell.file,
    basis_surface: ifcopenshell.entity_instance | None = None,
) -> ifcopenshell.entity_instance:
    """Converts an EdgeLoop to an IFC representation"""
    edges = [oriented_edge(e, f, basis_surface=basis_surface) for e in el.edge_list]
    return f.create_entity("IfcEdgeLoop", EdgeList=edges)


def poly_loop(pl: geo_cu.PolyLoop, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a PolyLoop to an IfcPolyLoop (a closed polygon of Cartesian points)."""
    return f.create_entity("IfcPolyLoop", Polygon=[cpt(f, p) for p in pl.polygon])
