import math

from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeWire
from OCC.Core.GC import GC_MakeArcOfCircle, GC_MakeArcOfEllipse
from OCC.Core.Geom import Geom_BSplineCurve
from OCC.Core.GeomAPI import GeomAPI_PointsToBSpline, GeomAPI_ProjectPointOnCurve
from OCC.Core.gp import gp_Ax2, gp_Circ, gp_Dir, gp_Elips, gp_Pnt
from OCC.Core.TColgp import TColgp_Array1OfPnt
from OCC.Core.TColStd import TColStd_Array1OfInteger, TColStd_Array1OfReal
from OCC.Core.TopAbs import TopAbs_FORWARD
from OCC.Core.TopoDS import TopoDS_Edge, TopoDS_Wire

from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su
from ada.occ.exceptions import UnableToCreateCurveOCCGeom
from ada.occ.utils import point3d

OCC_RADIUS_TOL = 1e-9  # geometric tolerance in model units


def _occ_bspline_curve_from_geom(curve_geom) -> Geom_BSplineCurve:
    """Build an OCC ``Geom_BSplineCurve`` from an adapy ``BSplineCurveWithKnots`` (rational supported)."""
    n_poles = len(curve_geom.control_points_list)
    poles = TColgp_Array1OfPnt(1, n_poles)
    for i, cp in enumerate(curve_geom.control_points_list, start=1):
        poles.SetValue(i, point3d(cp))
    n_knots = len(curve_geom.knots)
    knots = TColStd_Array1OfReal(1, n_knots)
    mults = TColStd_Array1OfInteger(1, n_knots)
    for i, (k, m) in enumerate(zip(curve_geom.knots, curve_geom.knot_multiplicities), start=1):
        knots.SetValue(i, float(k))
        mults.SetValue(i, int(m))
    degree = int(curve_geom.degree)
    if isinstance(curve_geom, geo_cu.RationalBSplineCurveWithKnots):
        weights = TColStd_Array1OfReal(1, n_poles)
        for i, w in enumerate(curve_geom.weights_data, start=1):
            weights.SetValue(i, float(w))
        return Geom_BSplineCurve(poles, weights, knots, mults, degree, False)
    return Geom_BSplineCurve(poles, knots, mults, degree, False)


def make_edge_from_line(geom: geo_cu.Edge | geo_cu.ArcLine) -> TopoDS_Edge:
    if isinstance(geom, geo_cu.ArcLine):
        a_arc_of_circle = GC_MakeArcOfCircle(point3d(geom.start), point3d(geom.midpoint), point3d(geom.end))
        return BRepBuilderAPI_MakeEdge(a_arc_of_circle.Value()).Edge()
    elif isinstance(geom, (geo_cu.BSplineCurveWithKnots, geo_cu.RationalBSplineCurveWithKnots)):
        # A plate-boundary spline spans exactly corner->corner, so the full-curve edge is the edge.
        return BRepBuilderAPI_MakeEdge(_occ_bspline_curve_from_geom(geom)).Edge()
    else:
        return BRepBuilderAPI_MakeEdge(point3d(geom.start), point3d(geom.end)).Edge()


def segments_to_edges(
    segments: list[geo_cu.Edge | geo_cu.ArcLine],
) -> list[TopoDS_Edge]:
    return [make_edge_from_line(seg) for seg in segments]


def make_edge_from_edge(edge: geo_cu.Edge) -> TopoDS_Edge:
    """
    Create an OCC edge from an adapy Edge.

    Args:
        edge: Can be Edge, OrientedEdge, or EdgeCurve

    Returns:
        OCC TopoDS_Edge
    """
    from ada.config import logger

    def _points_equal(a, b, tol=1e-6):
        try:
            return all(abs(x - y) <= tol for x, y in zip(a, b))
        except Exception:
            return False

    # Check if this is an OrientedEdge with an edge_element
    if isinstance(edge, geo_cu.OrientedEdge) and hasattr(edge, "edge_element"):
        edge_element = edge.edge_element

        # If edge_element is an EdgeCurve with geometry, use it
        if isinstance(edge_element, geo_cu.EdgeCurve) and hasattr(edge_element, "edge_geometry"):
            curve_geom = edge_element.edge_geometry

            logger.debug(f"Creating edge from {type(curve_geom).__name__}: start={edge.start}, end={edge.end}")

            # Handle different curve types
            if isinstance(curve_geom, geo_cu.Line):
                # Use line geometry
                p1 = point3d(edge.start)
                p2 = point3d(edge.end)
                edge_maker = BRepBuilderAPI_MakeEdge(p1, p2)
            elif isinstance(curve_geom, geo_cu.Circle):
                # Use circle geometry. The OCC point-trim overload always walks the
                # circle's POSITIVE parametric direction from P1 to P2, so the
                # occupied arc must be expressed in that form: the EdgeCurve's own
                # endpoints traversed positively iff ``same_sense``; for a
                # reversed-sense arc, reversing the circle's axis flips its
                # parametric direction so the same overload picks the correct
                # (complementary) arc. Using the OrientedEdge's endpoints here was
                # wrong twice over — readers differ on whether they pre-swap them,
                # and the arc point-set never depends on traversal orientation.
                axis_dir = gp_Dir(*curve_geom.position.axis)
                ec_same_sense = bool(getattr(edge_element, "same_sense", True))
                if not ec_same_sense:
                    axis_dir = axis_dir.Reversed()
                circle_origin = gp_Ax2(gp_Pnt(*curve_geom.position.location), axis_dir)
                circle = gp_Circ(circle_origin, curve_geom.radius)
                arc_start = getattr(edge_element, "start", edge.start)
                arc_end = getattr(edge_element, "end", edge.end)

                # If start and end are equal (full circle), create a full circle edge.
                # Otherwise create an arc between start and end.
                if _points_equal(arc_start, arc_end):
                    edge_maker = BRepBuilderAPI_MakeEdge(circle)
                else:
                    # Prefer the SAT-recorded parametric trim values
                    # over OCC's 3D-point recovery: a circle has *two*
                    # arcs between any two non-coincident points, and
                    # the point-trim overload picks based on the
                    # circle's natural parametric direction — wrong
                    # half the time on long thin face boundaries
                    # (manifests as a "hole" in the BRepMesh
                    # tessellation when the long arc is selected).
                    t_start = getattr(edge, "t_start", None)
                    t_end = getattr(edge, "t_end", None)
                    if t_start is not None and t_end is not None:
                        # SAT params are canonical w.r.t. the UNREVERSED curve.
                        circle_fwd = gp_Circ(
                            gp_Ax2(gp_Pnt(*curve_geom.position.location), gp_Dir(*curve_geom.position.axis)),
                            curve_geom.radius,
                        )
                        edge_maker = BRepBuilderAPI_MakeEdge(circle_fwd, float(t_start), float(t_end))
                    else:
                        edge_maker = BRepBuilderAPI_MakeEdge(circle, point3d(arc_start), point3d(arc_end))
            elif isinstance(curve_geom, (geo_cu.BSplineCurveWithKnots, geo_cu.RationalBSplineCurveWithKnots)):
                # Build an OCC BSpline curve from the adapy representation
                try:
                    n_poles = len(curve_geom.control_points_list)
                    poles = TColgp_Array1OfPnt(1, n_poles)
                    for i, cp in enumerate(curve_geom.control_points_list, start=1):
                        poles.SetValue(i, point3d(cp))

                    n_knots = len(curve_geom.knots)
                    knots = TColStd_Array1OfReal(1, n_knots)
                    mults = TColStd_Array1OfInteger(1, n_knots)
                    for i, (k, m) in enumerate(zip(curve_geom.knots, curve_geom.knot_multiplicities), start=1):
                        knots.SetValue(i, float(k))
                        mults.SetValue(i, int(m))

                    degree = int(curve_geom.degree)

                    # Rational vs non-rational
                    if isinstance(curve_geom, geo_cu.RationalBSplineCurveWithKnots):
                        weights = TColStd_Array1OfReal(1, n_poles)
                        for i, w in enumerate(curve_geom.weights_data, start=1):
                            weights.SetValue(i, float(w))
                        occ_bs = Geom_BSplineCurve(poles, weights, knots, mults, degree, False)
                    else:
                        occ_bs = Geom_BSplineCurve(poles, knots, mults, degree, False)

                    p1 = point3d(edge.start)
                    p2 = point3d(edge.end)

                    # If start and end are identical, create a full-curve edge (closed loop)
                    if _points_equal(edge.start, edge.end):
                        edge_maker = BRepBuilderAPI_MakeEdge(occ_bs)
                    elif (
                        getattr(edge, "t_start", None) is not None
                        and getattr(edge, "t_end", None) is not None
                        and abs(float(edge.t_end) - float(edge.t_start)) > 1e-12
                    ):
                        # Prefer the SAT-recorded parametric trim
                        # values over the project-from-3D-points path
                        # below: the projection is unreliable on
                        # self-intersecting BSplines (multiple
                        # parameters can map to the same point) and
                        # has accumulated numerical error in the
                        # projector. SAT stores the canonical
                        # parameters at edge.chunks[7]/[9].
                        edge_maker = BRepBuilderAPI_MakeEdge(occ_bs, float(edge.t_start), float(edge.t_end))
                    else:
                        # Project the points onto the curve to obtain parameters
                        proj1 = GeomAPI_ProjectPointOnCurve(p1, occ_bs)
                        proj2 = GeomAPI_ProjectPointOnCurve(p2, occ_bs)

                        if proj1.NbPoints() == 0 or proj2.NbPoints() == 0:
                            # Fallback to straight line if projection fails
                            logger.warning(
                                "Failed to project points on BSpline curve; attempting interpolation fallback"
                            )
                            # Try interpolation fallback before straight line
                            interp = GeomAPI_PointsToBSpline(poles)
                            occ_bs_fallback = interp.Curve()
                            if _points_equal(edge.start, edge.end):
                                edge_maker = BRepBuilderAPI_MakeEdge(occ_bs_fallback)
                            else:
                                proj1b = GeomAPI_ProjectPointOnCurve(p1, occ_bs_fallback)
                                proj2b = GeomAPI_ProjectPointOnCurve(p2, occ_bs_fallback)
                                if proj1b.NbPoints() == 0 or proj2b.NbPoints() == 0:
                                    logger.warning(
                                        "Interpolation fallback projection also failed; using straight line approximation"
                                    )
                                    edge_maker = BRepBuilderAPI_MakeEdge(p1, p2)
                                else:
                                    u1 = proj1b.LowerDistanceParameter()
                                    u2 = proj2b.LowerDistanceParameter()
                                    if abs(u1 - u2) <= 1e-12:
                                        edge_maker = BRepBuilderAPI_MakeEdge(occ_bs_fallback)
                                    else:
                                        edge_maker = BRepBuilderAPI_MakeEdge(occ_bs_fallback, u1, u2)
                        else:
                            u1 = proj1.LowerDistanceParameter()
                            u2 = proj2.LowerDistanceParameter()
                            # If parameters are effectively identical, avoid zero-length by using full curve
                            if abs(u1 - u2) <= 1e-12:
                                edge_maker = BRepBuilderAPI_MakeEdge(occ_bs)
                            else:
                                edge_maker = BRepBuilderAPI_MakeEdge(occ_bs, u1, u2)
                except Exception as ex:
                    logger.warning(f"BSpline curve edge creation failed ({ex}); attempting interpolation fallback")
                    try:
                        # Build with interpolation from poles only
                        n_poles = len(curve_geom.control_points_list)
                        poles = TColgp_Array1OfPnt(1, n_poles)
                        for i, cp in enumerate(curve_geom.control_points_list, start=1):
                            poles.SetValue(i, point3d(cp))
                        interp = GeomAPI_PointsToBSpline(poles)
                        occ_bs_fallback = interp.Curve()
                        p1 = point3d(edge.start)
                        p2 = point3d(edge.end)
                        if _points_equal(edge.start, edge.end):
                            edge_maker = BRepBuilderAPI_MakeEdge(occ_bs_fallback)
                        else:
                            proj1b = GeomAPI_ProjectPointOnCurve(p1, occ_bs_fallback)
                            proj2b = GeomAPI_ProjectPointOnCurve(p2, occ_bs_fallback)
                            if proj1b.NbPoints() == 0 or proj2b.NbPoints() == 0:
                                logger.warning(
                                    "Interpolation fallback projection failed; using straight line approximation"
                                )
                                edge_maker = BRepBuilderAPI_MakeEdge(p1, p2)
                            else:
                                u1 = proj1b.LowerDistanceParameter()
                                u2 = proj2b.LowerDistanceParameter()
                                if abs(u1 - u2) <= 1e-12:
                                    edge_maker = BRepBuilderAPI_MakeEdge(occ_bs_fallback)
                                else:
                                    edge_maker = BRepBuilderAPI_MakeEdge(occ_bs_fallback, u1, u2)
                    except Exception as ex2:
                        logger.warning(
                            f"BSpline interpolation fallback failed ({ex2}); using straight line approximation"
                        )
                        p1 = point3d(edge.start)
                        p2 = point3d(edge.end)
                        edge_maker = BRepBuilderAPI_MakeEdge(p1, p2)
            elif isinstance(curve_geom, geo_cu.Ellipse):
                # Ellipse geometry support
                try:
                    pos = curve_geom.position
                    ax2 = (
                        gp_Ax2(gp_Pnt(*pos.location), gp_Dir(*pos.axis), gp_Dir(*pos.ref_direction))
                        if hasattr(pos, "ref_direction")
                        else gp_Ax2(gp_Pnt(*pos.location), gp_Dir(*pos.axis))
                    )
                    el = gp_Elips(ax2, float(curve_geom.semi_axis1), float(curve_geom.semi_axis2))
                    if _points_equal(edge.start, edge.end):
                        edge_maker = BRepBuilderAPI_MakeEdge(el)
                    else:
                        edge_maker = BRepBuilderAPI_MakeEdge(el, point3d(edge.start), point3d(edge.end))
                except Exception as ex:
                    logger.warning(f"Ellipse edge creation failed ({ex}), using straight line approximation")
                    p1 = point3d(edge.start)
                    p2 = point3d(edge.end)
                    edge_maker = BRepBuilderAPI_MakeEdge(p1, p2)
            else:
                # Fallback to straight line for unsupported curve types
                logger.warning(f"Unsupported curve type {type(curve_geom).__name__}, using straight line")
                p1 = point3d(edge.start)
                p2 = point3d(edge.end)
                edge_maker = BRepBuilderAPI_MakeEdge(p1, p2)
        else:
            # No curve geometry, use straight line
            logger.debug(f"No curve geometry, using straight line: start={edge.start}, end={edge.end}")
            p1 = point3d(edge.start)
            p2 = point3d(edge.end)
            edge_maker = BRepBuilderAPI_MakeEdge(p1, p2)
    else:
        # Simple Edge, use straight line
        logger.debug(f"Simple edge, using straight line: start={edge.start}, end={edge.end}")
        p1 = point3d(edge.start)
        p2 = point3d(edge.end)
        edge_maker = BRepBuilderAPI_MakeEdge(p1, p2)

    # Check if edge creation was successful
    if not edge_maker.IsDone():
        # Curve-edge construction failed — most often a near-degenerate arc: a sub-mm
        # arc on a huge-radius cylinder, where OCC can't recover the parametric arc
        # from two nearly-coincident points (common on real-CAD cylindrical slivers).
        # Fall back to a straight chord between the endpoints: visually identical at
        # that scale, and it lets the face wire close instead of dropping the face.
        p1 = point3d(edge.start)
        p2 = point3d(edge.end)
        chord = BRepBuilderAPI_MakeEdge(p1, p2)
        if chord.IsDone():
            occ_edge = chord.Edge()
            occ_edge.Orientation(TopAbs_FORWARD)
            return occ_edge
        # Truly degenerate (start == end): let the wire builder skip it.
        error_msg = f"Failed to create edge from {type(edge).__name__}: start={edge.start}, end={edge.end}"
        if isinstance(edge, geo_cu.OrientedEdge) and hasattr(edge, "edge_element"):
            if isinstance(edge.edge_element, geo_cu.EdgeCurve):
                error_msg += f", curve_type={type(edge.edge_element.edge_geometry).__name__}"
        raise UnableToCreateCurveOCCGeom(error_msg)

    occ_edge = edge_maker.Edge()
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
    r = circle.radius

    # ---- Validate radius ----
    if r is None:
        raise ValueError(f"Circle radius is None: {circle}")

    if isinstance(r, float) and (math.isnan(r) or math.isinf(r)):
        raise ValueError(f"Circle radius invalid (NaN/Inf): {r} for circle={circle}")

    if r <= OCC_RADIUS_TOL:
        raise ValueError(
            f"Circle radius must be > {OCC_RADIUS_TOL}, got {r}. Circle={circle}, origin={circle.position.location}"
        )

    # ---- Build OCC circle ----
    circle_origin = gp_Ax2(
        gp_Pnt(*circle.position.location),
        gp_Dir(*circle.position.axis),
    )

    occ_circle = gp_Circ(circle_origin, float(r))

    circle_edge = BRepBuilderAPI_MakeEdge(occ_circle).Edge()

    wire_builder = BRepBuilderAPI_MakeWire()
    wire_builder.Add(circle_edge)
    wire_builder.Build()

    return wire_builder.Wire()


def make_wire_from_ellipse(ellipse: geo_cu.Ellipse) -> TopoDS_Wire:
    pos = ellipse.position
    ax2 = gp_Ax2(gp_Pnt(*pos.location), gp_Dir(*pos.axis), gp_Dir(*getattr(pos, "ref_direction", pos.axis)))
    el = gp_Elips(ax2, float(ellipse.semi_axis1), float(ellipse.semi_axis2))

    ellipse_edge = BRepBuilderAPI_MakeEdge(el).Edge()
    wire = BRepBuilderAPI_MakeWire()
    wire.Add(ellipse_edge)
    wire.Build()
    return wire.Wire()


def make_wire_from_trimmed_curve(tc: geo_cu.TrimmedCurve) -> TopoDS_Wire:
    """Build a bounded wire from a TrimmedCurve.

    Trims may be Cartesian points or parameter values (angles for conic bases,
    normalized to radians at read time; line parameters scale the direction
    vector, whose IfcVector magnitude the reader preserves). Bases: Line,
    Circle, Ellipse."""
    import numpy as np

    from ada.geom.points import Point

    def _p3(v) -> list[float]:
        vals = [float(x) for x in v]
        return vals + [0.0] * (3 - len(vals))

    basis = tc.basis_curve
    t1, t2 = tc.trim1, tc.trim2
    sense = bool(tc.sense_agreement)

    if isinstance(basis, geo_cu.Line):

        def _line_pt(t) -> gp_Pnt:
            if isinstance(t, Point):
                return point3d(t)
            w = np.asarray(_p3(basis.pnt)) + float(t) * np.asarray(_p3(basis.dir))
            return gp_Pnt(*[float(v) for v in w])

        edge = BRepBuilderAPI_MakeEdge(_line_pt(t1), _line_pt(t2)).Edge()
    elif isinstance(basis, (geo_cu.Circle, geo_cu.Ellipse)):
        pos = basis.position
        loc = np.asarray(_p3(pos.location))
        z = np.asarray(_p3(pos.axis) if pos.axis is not None else [0, 0, 1])
        x = np.asarray(_p3(pos.ref_direction) if getattr(pos, "ref_direction", None) is not None else [1, 0, 0])
        z = z / (np.linalg.norm(z) or 1.0)
        x = x / (np.linalg.norm(x) or 1.0)
        y = np.cross(z, x)
        # Parameter trims are measured from the position's x axis — build the
        # gp frame with the explicit XDirection (the 2-arg gp_Ax2 picks an
        # arbitrary one).
        ax2 = gp_Ax2(gp_Pnt(*loc), gp_Dir(*z), gp_Dir(*x))
        if isinstance(basis, geo_cu.Circle):
            sa1 = sa2 = float(basis.radius)
            conic = gp_Circ(ax2, sa1)
        else:
            sa1, sa2 = float(basis.semi_axis1), float(basis.semi_axis2)
            conic = gp_Elips(ax2, sa1, sa2)

        def _conic_pt(t) -> gp_Pnt:
            if isinstance(t, Point):
                return point3d(t)
            w = loc + sa1 * np.cos(float(t)) * x + sa2 * np.sin(float(t)) * y
            return gp_Pnt(*[float(v) for v in w])

        if isinstance(basis, geo_cu.Circle):
            arc = GC_MakeArcOfCircle(conic, _conic_pt(t1), _conic_pt(t2), sense).Value()
        else:
            arc = GC_MakeArcOfEllipse(conic, _conic_pt(t1), _conic_pt(t2), sense).Value()
        edge = BRepBuilderAPI_MakeEdge(arc).Edge()
    else:
        raise NotImplementedError(f"TrimmedCurve OCC build not implemented for basis {type(basis)}")

    wire = BRepBuilderAPI_MakeWire()
    wire.Add(edge)
    wire.Build()
    return wire.Wire()


def make_wire_from_composite_curve(cc: geo_cu.CompositeCurve) -> TopoDS_Wire:
    """Concatenate each segment's parent-curve wire into a single wire."""
    wire_builder = BRepBuilderAPI_MakeWire()
    for seg in cc.segments:
        wire_builder.Add(make_wire_from_curve(seg.parent_curve))
    return wire_builder.Wire()


def make_wire_from_edge_loop(edge_loop: geo_cu.EdgeLoop) -> TopoDS_Wire:
    from ada.config import logger

    wire = BRepBuilderAPI_MakeWire()
    skipped_edges = 0
    added_edges = 0

    def _pts_equal(a, b, tol: float = 1e-9) -> bool:
        try:
            return all(abs(x - y) <= tol for x, y in zip(a, b))
        except Exception:
            try:
                ta = tuple(a)
                tb = tuple(b)
                return all(abs(x - y) <= tol for x, y in zip(ta, tb))
            except Exception:
                return False

    # Special-case single-edge loops that represent closed curves (circle/ellipse/bspline):
    if len(edge_loop.edge_list) == 1:
        para_edge = edge_loop.edge_list[0]
        logger.debug(f"Single-edge loop detected. Edge type: {type(para_edge).__name__}")
        try:
            # If the underlying geometry is an EdgeCurve, check its curve type
            if isinstance(para_edge, geo_cu.OrientedEdge) and hasattr(para_edge, "edge_element"):
                ee = para_edge.edge_element
                logger.debug(f"Edge element type: {type(ee).__name__}")
                if isinstance(ee, geo_cu.EdgeCurve):
                    geom = ee.edge_geometry
                    if isinstance(geom, geo_cu.Circle):
                        logger.debug("Creating full-circle wire from Circle geometry")
                        return make_wire_from_circle(geom)
                    if isinstance(geom, geo_cu.Ellipse) and _pts_equal(para_edge.start, para_edge.end):
                        logger.debug("Creating full-ellipse wire from Ellipse geometry")
                        return make_wire_from_ellipse(geom)
                    if isinstance(geom, (geo_cu.BSplineCurveWithKnots, geo_cu.RationalBSplineCurveWithKnots)):
                        # If marked closed or start==end, treat as full curve edge
                        if getattr(geom, "closed_curve", False) or _pts_equal(para_edge.start, para_edge.end):
                            logger.debug("Creating full B-Spline wire from closed BSpline geometry")
                            occ_edge = make_edge_from_edge(para_edge)
                            wire = BRepBuilderAPI_MakeWire()
                            wire.Add(occ_edge)
                            wire.Build()
                            return wire.Wire()
        except Exception as ex:
            # fall back to normal loop handling if any assumption fails
            logger.debug(f"Special-case handling failed: {ex}, falling back to normal loop handling")
            pass

    occ_edges = []
    for i, para_edge in enumerate(edge_loop.edge_list):
        try:
            occ_edge = make_edge_from_edge(para_edge)
            occ_edges.append(occ_edge)
            wire.Add(occ_edge)
            added_edges += 1
        except (RuntimeError, UnableToCreateCurveOCCGeom) as e:
            # Skip degenerate edges (e.g., where start and end points are the same)
            skipped_edges += 1
            logger.debug(f"Skipped degenerate edge {i}: {e}")
            continue

    if skipped_edges > 0:
        logger.debug(f"Skipped {skipped_edges} degenerate edges when creating wire (added {added_edges} edges)")

    if added_edges == 0:
        raise UnableToCreateCurveOCCGeom(
            f"No valid edges could be created from EdgeLoop with {len(edge_loop.edge_list)} edges"
        )

    wire.Build()

    # Sequential MakeWire.Add chains each edge to the previous one's free vertex, so an
    # out-of-order edge list (common in SAT/STEP face-bound loops) either fails outright
    # (IsDone False) OR silently drops the edges that don't chain and returns an *open*
    # wire (IsDone True). An EdgeLoop is a closed loop by definition, so a closed
    # sequential wire is the only clean success; anything else gets a ShapeFix_Wire
    # reorder over the full edge set before we give up (these otherwise drop as holes).
    seq_wire = wire.Wire() if wire.IsDone() else None
    if seq_wire is not None and seq_wire.Closed():
        return seq_wire

    if len(occ_edges) >= 2:
        try:
            from OCC.Core.ShapeExtend import ShapeExtend_WireData
            from OCC.Core.ShapeFix import ShapeFix_Wire

            wd = ShapeExtend_WireData()
            for e in occ_edges:
                wd.Add(e)
            sfw = ShapeFix_Wire()
            sfw.Load(wd)
            sfw.SetMaxTolerance(1.0e-3)
            sfw.FixReorder()
            sfw.FixConnected()
            sfw.FixClosed()
            fixed = sfw.Wire()
            # Reorder reconnects the FULL edge set, so it is at least as complete as the
            # sequential wire (which silently drops non-chaining edges). Prefer any
            # non-null reordered wire — even if OCC's Closed() flag stays false, the
            # downstream BRepBuilderAPI_MakeFace tolerates the residual sub-tolerance gap.
            if fixed is not None and not fixed.IsNull():
                logger.debug("Wire rebuilt via ShapeFix_Wire reorder (sequential build gave open/failed wire)")
                return fixed
        except Exception as ex:  # noqa: BLE001 - fall through to the original behaviour
            logger.debug(f"ShapeFix_Wire reorder fallback failed: {ex}")

    if seq_wire is not None:
        return seq_wire  # preserve prior behaviour: an open-but-built wire still beats nothing

    logger.error(f"Wire creation failed after adding {added_edges} edges (skipped {skipped_edges})")
    raise UnableToCreateCurveOCCGeom(f"Failed to build wire from {added_edges} edges")


def make_wire_from_curve(outer_curve: geo_cu.CURVE_GEOM_TYPES):
    if isinstance(outer_curve, geo_cu.IndexedPolyCurve):
        return make_wire_from_indexed_poly_curve_geom(outer_curve)
    elif isinstance(outer_curve, geo_cu.Circle):
        return make_wire_from_circle(outer_curve)
    elif isinstance(outer_curve, geo_cu.Edge):
        return segments_to_wire([outer_curve])
    elif isinstance(outer_curve, geo_cu.TrimmedCurve):
        return make_wire_from_trimmed_curve(outer_curve)
    elif isinstance(outer_curve, geo_cu.CompositeCurve):
        return make_wire_from_composite_curve(outer_curve)
    elif isinstance(outer_curve, geo_cu.GradientCurve):
        return make_wire_from_gradient_curve(outer_curve)
    elif isinstance(outer_curve, geo_cu.PolyLine):
        return make_wire_from_polyline(outer_curve)
    elif isinstance(outer_curve, geo_cu.GeometricCurveSet):
        # Loose curve collection (STEP wireframe body): the members are
        # independent curves, so build a compound of wires rather than
        # forcing them into one connected wire.
        from OCC.Core.BRep import BRep_Builder
        from OCC.Core.TopoDS import TopoDS_Compound

        builder = BRep_Builder()
        compound = TopoDS_Compound()
        builder.MakeCompound(compound)
        for element in outer_curve.elements:
            builder.Add(compound, make_wire_from_curve(element))
        return compound
    else:
        raise NotImplementedError(f"Unsupported curve type {type(outer_curve)}")


def make_wire_from_gradient_curve(curve: geo_cu.GradientCurve) -> TopoDS_Wire:
    """Alignment directrix (IFC4x3 IfcGradientCurve): clothoid segments have no
    analytic OCC curve, so build a polyline wire from the shared alignment
    evaluator's sampling — the same discretization the adacpp backend sweeps."""
    import numpy as np

    from ada.cadit.ngeom._alignment_sweep import gradient_curve_points

    pts = gradient_curve_points(curve, n_per=100)
    wire_maker = BRepBuilderAPI_MakeWire()
    for a, b in zip(pts[:-1], pts[1:]):
        if float(np.linalg.norm(b - a)) < 1e-9:
            continue
        edge = BRepBuilderAPI_MakeEdge(
            gp_Pnt(float(a[0]), float(a[1]), float(a[2])), gp_Pnt(float(b[0]), float(b[1]), float(b[2]))
        ).Edge()
        wire_maker.Add(edge)
    return wire_maker.Wire()


def make_wire_from_polyline(curve: geo_cu.PolyLine) -> TopoDS_Wire:
    """A sampled polyline (e.g. an evaluated alignment reference curve) -> a polyline wire, so it
    round-trips through the OCC B-rep exporters (STEP GEOMETRIC_CURVE_SET, IFC)."""
    import numpy as np

    pts = [np.asarray(p, dtype=float) for p in curve.points]
    wire_maker = BRepBuilderAPI_MakeWire()
    added = 0
    for a, b in zip(pts[:-1], pts[1:]):
        if float(np.linalg.norm(b - a)) < 1e-9:
            continue
        edge = BRepBuilderAPI_MakeEdge(
            gp_Pnt(float(a[0]), float(a[1]), float(a[2])), gp_Pnt(float(b[0]), float(b[1]), float(b[2]))
        ).Edge()
        wire_maker.Add(edge)
        added += 1
    if added == 0:
        raise NotImplementedError("PolyLine has no non-degenerate segments to build a wire from")
    return wire_maker.Wire()


def make_wire_from_face_bound(face_bound: geo_su.FaceBound) -> TopoDS_Wire:
    if isinstance(face_bound.bound, geo_cu.PolyLoop):
        return make_wire_from_poly_loop(face_bound.bound)
    if isinstance(face_bound.bound, geo_cu.EdgeLoop):
        return make_wire_from_edge_loop(face_bound.bound)
    else:
        raise NotImplementedError("Only PolyLoop bounds are supported")
