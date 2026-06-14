import ifcopenshell

from ada.geom import curves as geo_cu
from ada.geom.direction import Direction
from ada.geom.points import Point


def get_curve(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.CURVE_GEOM_TYPES:
    if ifc_entity.is_a("IfcIndexedPolyCurve"):
        return indexed_poly_curve(ifc_entity)
    elif ifc_entity.is_a("IfcPolyline"):
        return poly_line(ifc_entity)
    elif ifc_entity.is_a("IfcRationalBSplineCurveWithKnots"):
        return rational_b_spline_curve_with_knots(ifc_entity)
    elif ifc_entity.is_a("IfcBSplineCurveWithKnots"):
        return b_spline_curve_with_knots(ifc_entity)
    elif ifc_entity.is_a("IfcTrimmedCurve"):
        return trimmed_curve(ifc_entity)
    elif ifc_entity.is_a("IfcLine"):
        return line(ifc_entity)
    elif ifc_entity.is_a("IfcCircle"):
        return circle(ifc_entity)
    elif ifc_entity.is_a("IfcEllipse"):
        return ellipse(ifc_entity)
    elif ifc_entity.is_a("IfcSurfaceCurve"):
        # The 3D curve is the geometric master; the attached p-curve is recovered
        # separately by oriented_edge() onto the OrientedEdge.
        return get_curve(ifc_entity.Curve3D)
    else:
        raise NotImplementedError(f"Geometry type {ifc_entity.is_a()} not implemented")


def line(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.Line:
    return geo_cu.Line(pnt=Point(ifc_entity.Pnt.Coordinates), dir=Direction(ifc_entity.Dir.Orientation.DirectionRatios))


def circle(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.Circle:
    from .placement import axis3d

    return geo_cu.Circle(position=axis3d(ifc_entity.Position), radius=ifc_entity.Radius)


def ellipse(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.Ellipse:
    from .placement import axis3d

    return geo_cu.Ellipse(
        position=axis3d(ifc_entity.Position),
        semi_axis1=ifc_entity.SemiAxis1,
        semi_axis2=ifc_entity.SemiAxis2,
    )


def _trim_select(trim) -> "Point | float":
    """One IfcTrimmingSelect entry: a Cartesian point or a parameter value. IFC allows up to
    two (point AND parameter) — we keep the Cartesian point when present, else the parameter."""
    for item in trim:
        if hasattr(item, "is_a") and item.is_a("IfcCartesianPoint"):
            return Point(item.Coordinates)
    return float(trim[0].wrappedValue if hasattr(trim[0], "wrappedValue") else trim[0])


def trimmed_curve(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.TrimmedCurve:
    return geo_cu.TrimmedCurve(
        basis_curve=get_curve(ifc_entity.BasisCurve),
        trim1=_trim_select(ifc_entity.Trim1),
        trim2=_trim_select(ifc_entity.Trim2),
        sense_agreement=ifc_entity.SenseAgreement,
        master_representation=ifc_entity.MasterRepresentation,
    )


def pcurve_2d_from_surface_curve(surface_curve: ifcopenshell.entity_instance) -> geo_cu.Pcurve2dBSpline | None:
    """Recover the UV p-curve (2D B-spline) attached to an IfcSurfaceCurve."""
    associated = getattr(surface_curve, "AssociatedGeometry", None)
    if not associated:
        return None
    ref = associated[0].ReferenceCurve  # IfcPcurve.ReferenceCurve
    if not ref.is_a("IfcBSplineCurveWithKnots"):
        return None
    weights = list(ref.WeightsData) if ref.is_a("IfcRationalBSplineCurveWithKnots") else None
    return geo_cu.Pcurve2dBSpline(
        degree=ref.Degree,
        control_points_2d=[(float(p.Coordinates[0]), float(p.Coordinates[1])) for p in ref.ControlPointsList],
        knots=list(ref.Knots),
        knot_multiplicities=list(ref.KnotMultiplicities),
        weights=weights,
        closed=bool(ref.ClosedCurve),
    )


def b_spline_curve_with_knots(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.BSplineCurveWithKnots:
    return geo_cu.BSplineCurveWithKnots(
        degree=ifc_entity.Degree,
        control_points_list=[Point(p.Coordinates) for p in ifc_entity.ControlPointsList],
        curve_form=geo_cu.BSplineCurveFormEnum(ifc_entity.CurveForm),
        closed_curve=ifc_entity.ClosedCurve,
        self_intersect=ifc_entity.SelfIntersect,
        knot_multiplicities=list(ifc_entity.KnotMultiplicities),
        knots=list(ifc_entity.Knots),
        knot_spec=geo_cu.KnotType.from_str(ifc_entity.KnotSpec),
    )


def rational_b_spline_curve_with_knots(
    ifc_entity: ifcopenshell.entity_instance,
) -> geo_cu.RationalBSplineCurveWithKnots:
    return geo_cu.RationalBSplineCurveWithKnots(
        degree=ifc_entity.Degree,
        control_points_list=[Point(p.Coordinates) for p in ifc_entity.ControlPointsList],
        curve_form=geo_cu.BSplineCurveFormEnum(ifc_entity.CurveForm),
        closed_curve=ifc_entity.ClosedCurve,
        self_intersect=ifc_entity.SelfIntersect,
        knot_multiplicities=list(ifc_entity.KnotMultiplicities),
        knots=list(ifc_entity.Knots),
        knot_spec=geo_cu.KnotType.from_str(ifc_entity.KnotSpec),
        weights_data=list(ifc_entity.WeightsData),
    )


def poly_line(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.PolyLine:
    return geo_cu.PolyLine([Point(x.Coordinates) for x in ifc_entity.Points])


def indexed_poly_curve(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.IndexedPolyCurve:
    pts = ifc_entity.Points.CoordList
    segments = []
    for segment in ifc_entity.Segments:
        value = [x - 1 for x in segment.wrappedValue]
        if segment.is_a("IfcLineIndex"):
            segments.append(geo_cu.Edge(pts[value[0]], pts[value[1]]))
        else:
            segments.append(geo_cu.ArcLine(pts[value[0]], pts[value[1]], pts[value[2]]))

    return geo_cu.IndexedPolyCurve(segments, ifc_entity.SelfIntersect)


def edge(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.Edge:
    start = Point(ifc_entity.EdgeStart.VertexGeometry.Coordinates)
    end = Point(ifc_entity.EdgeEnd.VertexGeometry.Coordinates)

    # An IfcEdgeCurve carries the actual trimming curve (e.g. a B-spline). Dropping
    # it (as a bare IfcEdge) leaves an OCC face with no p-curve, which tessellates to
    # a degenerate, near-zero-area mesh — the curved plate looks "missing".
    if ifc_entity.is_a("IfcEdgeCurve") and ifc_entity.EdgeGeometry is not None:
        return geo_cu.EdgeCurve(
            start=start,
            end=end,
            edge_geometry=get_curve(ifc_entity.EdgeGeometry),
            same_sense=ifc_entity.SameSense,
        )

    return geo_cu.Edge(start=start, end=end)


def oriented_edge(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.OrientedEdge:
    ee = ifc_entity.EdgeElement

    # Recover the UV p-curve when the edge geometry is an IfcSurfaceCurve — without
    # it the trimmed B-spline face tessellates degenerate (near-zero area).
    pcurve = None
    if ee.is_a("IfcEdgeCurve") and ee.EdgeGeometry is not None and ee.EdgeGeometry.is_a("IfcSurfaceCurve"):
        pcurve = pcurve_2d_from_surface_curve(ee.EdgeGeometry)

    return geo_cu.OrientedEdge(
        start=Point(ifc_entity.EdgeStart.VertexGeometry.Coordinates),
        end=Point(ifc_entity.EdgeEnd.VertexGeometry.Coordinates),
        edge_element=edge(ee),
        orientation=ifc_entity.Orientation,
        pcurve=pcurve,
    )


def edge_loop(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.EdgeLoop:
    return geo_cu.EdgeLoop([oriented_edge(e) for e in ifc_entity.EdgeList])
