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
    elif ifc_entity.is_a("IfcSegmentedReferenceCurve"):
        # Subtype of IfcCompositeCurve (via IfcGradientCurve) — must precede both.
        return segmented_reference_curve(ifc_entity)
    elif ifc_entity.is_a("IfcGradientCurve"):
        # Subtype of IfcCompositeCurve — must precede it.
        return gradient_curve(ifc_entity)
    elif ifc_entity.is_a("IfcCompositeCurve"):
        return composite_curve(ifc_entity)
    elif ifc_entity.is_a("IfcCurveSegment"):
        # A single alignment curve segment used directly as a representation item (an
        # IfcAlignmentSegment's 'Axis'/'Segment' body).
        return curve_segment(ifc_entity)
    elif ifc_entity.is_a("IfcLine"):
        return line(ifc_entity)
    elif ifc_entity.is_a("IfcClothoid"):
        return clothoid(ifc_entity)
    elif ifc_entity.is_a("IfcCosineSpiral"):
        return cosine_spiral(ifc_entity)
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
    # IfcLine.Dir is an IfcVector — its Magnitude scales the curve's
    # parameterization (P(t) = Pnt + t*Dir). Fold it into the stored
    # direction so parameter trims (IfcTrimmedCurve on a line basis)
    # evaluate correctly; consumers that only need the direction are
    # magnitude-agnostic.
    vec = ifc_entity.Dir
    mag = float(getattr(vec, "Magnitude", 1.0) or 1.0)
    ratios = tuple(float(x) * mag for x in vec.Orientation.DirectionRatios)
    return geo_cu.Line(pnt=Point(ifc_entity.Pnt.Coordinates), dir=Direction(ratios))


def circle(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.Circle:
    from .placement import axis2d_as_3d, axis3d

    pos = ifc_entity.Position
    position = axis2d_as_3d(pos) if pos.is_a("IfcAxis2Placement2D") else axis3d(pos)
    return geo_cu.Circle(position=position, radius=ifc_entity.Radius)


def clothoid(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.Clothoid:
    """IfcClothoid — 2D Euler spiral about its Position, signed ClothoidConstant A."""
    pos = ifc_entity.Position
    loc = pos.Location.Coordinates
    rd = pos.RefDirection.DirectionRatios if pos.RefDirection is not None else (1.0, 0.0)
    return geo_cu.Clothoid(
        location=(float(loc[0]), float(loc[1])),
        ref_direction=(float(rd[0]), float(rd[1])),
        clothoid_constant=float(ifc_entity.ClothoidConstant),
    )


def cosine_spiral(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.CosineSpiral:
    """IfcCosineSpiral — 2D transition spiral about its Position; curvature varies as a cosine.
    A1 = CosineTerm (required), A0 = ConstantTerm (optional)."""
    pos = ifc_entity.Position
    loc = pos.Location.Coordinates
    rd = pos.RefDirection.DirectionRatios if pos.RefDirection is not None else (1.0, 0.0)
    ct = ifc_entity.ConstantTerm
    return geo_cu.CosineSpiral(
        location=(float(loc[0]), float(loc[1])),
        ref_direction=(float(rd[0]), float(rd[1])),
        cosine_term=float(ifc_entity.CosineTerm),
        constant_term=float(ct) if ct is not None else None,
    )


def _measure(v) -> float:
    return float(v.wrappedValue if hasattr(v, "wrappedValue") else v)


def curve_segment(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.CurveSegment:
    """IfcCurveSegment — a parent curve over [SegmentStart, SegmentStart+SegmentLength] (arc length),
    positioned by a 2D placement. The placement is IfcAxis2Placement2D or IfcAxis2PlacementLinear;
    we read its planar Location + RefDirection (the linear placement's distance-along is resolved
    when the segment is evaluated along its parent base curve)."""
    pl = ifc_entity.Placement
    if pl.is_a("IfcAxis2Placement2D"):
        loc = pl.Location.Coordinates
        rd = pl.RefDirection.DirectionRatios if pl.RefDirection is not None else (1.0, 0.0)
        location = (float(loc[0]), float(loc[1]))
        ref_direction = (float(rd[0]), float(rd[1]))
    elif pl.is_a("IfcAxis2Placement3D"):
        # Cant segments of an IfcSegmentedReferenceCurve are placed by a 3D axis placement; the
        # extra (vertical) component of the location is the superelevation offset. Keep the full
        # 3D location + ref direction — planar consumers slice [:2].
        loc = pl.Location.Coordinates
        rd = pl.RefDirection.DirectionRatios if pl.RefDirection is not None else (1.0, 0.0, 0.0)
        location = tuple(float(c) for c in loc)
        ref_direction = tuple(float(c) for c in rd)
    else:  # IfcAxis2PlacementLinear — planar location resolved at evaluation; carry the ref dir
        rd = pl.RefDirection.DirectionRatios if pl.RefDirection is not None else (1.0, 0.0)
        location = (0.0, 0.0)
        ref_direction = (float(rd[0]), float(rd[1]))
    return geo_cu.CurveSegment(
        transition=ifc_entity.Transition,
        location=location,
        ref_direction=ref_direction,
        segment_start=_measure(ifc_entity.SegmentStart),
        segment_length=_measure(ifc_entity.SegmentLength),
        parent_curve=get_curve(ifc_entity.ParentCurve),
    )


def gradient_curve(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.GradientCurve:
    """IfcGradientCurve — vertical gradient (Segments, distance->height) over a horizontal BaseCurve."""
    return geo_cu.GradientCurve(
        base_curve=composite_curve(ifc_entity.BaseCurve),
        segments=[curve_segment(s) for s in ifc_entity.Segments],
        self_intersect=bool(ifc_entity.SelfIntersect),
    )


def segmented_reference_curve(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.SegmentedReferenceCurve:
    """IfcSegmentedReferenceCurve — cant (superelevation) segments over a BaseCurve (an
    IfcGradientCurve). The base gives x,y,z; the segments add the vertical cant offset."""
    return geo_cu.SegmentedReferenceCurve(
        base_curve=get_curve(ifc_entity.BaseCurve),
        segments=[curve_segment(s) for s in ifc_entity.Segments],
        self_intersect=bool(ifc_entity.SelfIntersect),
    )


def ellipse(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.Ellipse:
    from .placement import axis2d_as_3d, axis3d

    # Profile-plane ellipses (2D placement, no Axis attribute) lift into
    # the z=0 plane — same treatment as circle() above.
    pos = ifc_entity.Position
    position = axis2d_as_3d(pos) if pos.is_a("IfcAxis2Placement2D") else axis3d(pos)
    return geo_cu.Ellipse(
        position=position,
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


def composite_curve(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.CompositeCurve:
    # IFC4x3 alignment composite curves carry IfcCurveSegment (placement + parametric range, no
    # SameSense); classic composites carry IfcCompositeCurveSegment.
    segments = [
        (
            curve_segment(seg)
            if seg.is_a("IfcCurveSegment")
            else geo_cu.CompositeCurveSegment(
                parent_curve=get_curve(seg.ParentCurve),
                same_sense=seg.SameSense,
                transition=seg.Transition,
            )
        )
        for seg in ifc_entity.Segments
    ]
    return geo_cu.CompositeCurve(segments=segments, self_intersect=bool(ifc_entity.SelfIntersect))


def _plane_angle_scale(f) -> float:
    """Radians per file plane-angle unit (1.0 for SI radian files; 0.01745…
    for files declaring a conversion-based DEGREE unit)."""
    import ifcopenshell.util.unit as uu

    try:
        return float(uu.calculate_unit_scale(f, unit_type="PLANEANGLEUNIT"))
    except Exception:  # noqa: BLE001 - missing/odd unit assignment: assume radians
        return 1.0


def trimmed_curve(ifc_entity: ifcopenshell.entity_instance) -> geo_cu.TrimmedCurve:
    trim1 = _trim_select(ifc_entity.Trim1)
    trim2 = _trim_select(ifc_entity.Trim2)
    if ifc_entity.BasisCurve.is_a("IfcConic"):
        # Parameter trims on conics are angles expressed in the file's
        # plane-angle unit — normalize to radians at read time so
        # downstream evaluators need no unit context (the buildingSMART
        # curve-parameter samples ship one file in degrees, one in radians).
        scale = _plane_angle_scale(ifc_entity.file)
        if isinstance(trim1, float):
            trim1 *= scale
        if isinstance(trim2, float):
            trim2 *= scale
    return geo_cu.TrimmedCurve(
        basis_curve=get_curve(ifc_entity.BasisCurve),
        trim1=trim1,
        trim2=trim2,
        sense_agreement=ifc_entity.SenseAgreement,
        master_representation=ifc_entity.MasterRepresentation,
    )


def pcurve_2d_from_surface_curve(
    surface_curve: ifcopenshell.entity_instance,
    basis_surface: ifcopenshell.entity_instance | None = None,
) -> geo_cu.Pcurve2dBSpline | None:
    """Recover the UV p-curve (2D B-spline) attached to an IfcSurfaceCurve.

    ``basis_surface`` (the parent face's FaceSurface entity) filters the associated
    p-curves: a p-curve's UV coordinates are meaningful ONLY on its own BasisSurface.
    In a closed shell every edge is shared by two faces — without the filter the
    bottom-patch p-curve leaks onto e.g. the ruled side face using the same edge, and
    the backend then builds the side wire from foreign UV data (wire build failed)."""
    associated = getattr(surface_curve, "AssociatedGeometry", None)
    if not associated:
        return None
    for pc in associated:
        if basis_surface is not None and getattr(pc, "BasisSurface", None) != basis_surface:
            continue
        ref = pc.ReferenceCurve  # IfcPcurve.ReferenceCurve
        if not ref.is_a("IfcBSplineCurveWithKnots"):
            continue
        weights = list(ref.WeightsData) if ref.is_a("IfcRationalBSplineCurveWithKnots") else None
        return geo_cu.Pcurve2dBSpline(
            degree=ref.Degree,
            control_points_2d=[(float(p.Coordinates[0]), float(p.Coordinates[1])) for p in ref.ControlPointsList],
            knots=list(ref.Knots),
            knot_multiplicities=list(ref.KnotMultiplicities),
            weights=weights,
            closed=bool(ref.ClosedCurve),
        )
    return None


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
            # IfcLineIndex is a POLYLINE through all its points (>=2) — one edge per
            # consecutive pair. Taking only value[0]/value[1] dropped every intermediate
            # vertex, collapsing multi-point runs (e.g. an I-section's flange outline).
            for a, b in zip(value[:-1], value[1:]):
                segments.append(geo_cu.Edge(pts[a], pts[b]))
        else:  # IfcArcIndex: exactly (start, mid, end)
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


def oriented_edge(
    ifc_entity: ifcopenshell.entity_instance,
    basis_surface: ifcopenshell.entity_instance | None = None,
) -> geo_cu.OrientedEdge:
    ee = ifc_entity.EdgeElement

    # Recover the UV p-curve when the edge geometry is an IfcSurfaceCurve — without
    # it the trimmed B-spline face tessellates degenerate (near-zero area). Only the
    # p-curve lying on the parent face's own surface is taken (see the filter above).
    pcurve = None
    if ee.is_a("IfcEdgeCurve") and ee.EdgeGeometry is not None and ee.EdgeGeometry.is_a("IfcSurfaceCurve"):
        pcurve = pcurve_2d_from_surface_curve(ee.EdgeGeometry, basis_surface=basis_surface)

    return geo_cu.OrientedEdge(
        start=Point(ifc_entity.EdgeStart.VertexGeometry.Coordinates),
        end=Point(ifc_entity.EdgeEnd.VertexGeometry.Coordinates),
        edge_element=edge(ee),
        orientation=ifc_entity.Orientation,
        pcurve=pcurve,
    )


def edge_loop(
    ifc_entity: ifcopenshell.entity_instance,
    basis_surface: ifcopenshell.entity_instance | None = None,
) -> geo_cu.EdgeLoop:
    return geo_cu.EdgeLoop([oriented_edge(e, basis_surface=basis_surface) for e in ifc_entity.EdgeList])
