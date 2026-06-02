from OCC.Core.BRep import BRep_Tool
from OCC.Core.Geom import Geom_BSplineCurve, Geom_Circle, Geom_Line, Geom_Surface
from OCC.Core.Geom2d import Geom2d_TrimmedCurve
from OCC.Core.Geom2dConvert import geom2dconvert
from OCC.Core.gp import gp_Dir, gp_Pnt
from OCC.Core.TopAbs import TopAbs_EDGE, TopAbs_FORWARD, TopAbs_WIRE
from OCC.Core.TopExp import TopExp_Explorer, topexp
from OCC.Core.TopoDS import TopoDS_Edge, TopoDS_Face, TopoDS_Wire, topods

from ada import Direction
from ada.occ.step.geom.helpers import (
    array1_to_int_list,
    array1_to_list,
    array1_to_point_list,
)
from ada.config import logger
from ada.geom import curves as geo_cu
from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point


def _extract_pcurve(edge: TopoDS_Edge, face: TopoDS_Face) -> geo_cu.Pcurve2dBSpline | None:
    """Extract the 2D UV-space curve of ``edge`` on ``face``.

    OCC attaches a ``Geom2d_Curve`` per (edge, face) pair when the
    wire is built on that face; ``BRepOffsetAPI_ThruSections`` does
    this automatically. The 2D curve is what the OCC face builder
    actually needs to bound a BSpline-surface face — without it, the
    wire on the surface is unparameterised and ``BRepBuilderAPI_MakeFace``
    yields a face with zero usable interior. We convert whatever 2D
    curve OCC supplied (line, circle, trimmed B-spline, etc.) to a
    BSpline form via ``geom2dconvert.CurveToBSplineCurve`` so it can
    be serialised as a :class:`Pcurve2dBSpline`.

    Returns ``None`` when OCC reports no curve-on-surface for the
    edge (rare for a wire that came directly from a face) or when
    the conversion to BSpline fails.
    """
    try:
        c2d, u_first, u_last = BRep_Tool.CurveOnSurface(edge, face)
    except Exception:
        return None
    if c2d is None:
        return None
    # Wrap in a TrimmedCurve before converting to BSpline form.
    # Without trimming, ``geom2dconvert.CurveToBSplineCurve`` raises
    # Standard_DomainError on infinite-range curves (Geom2d_Line has
    # parameter range (-inf, +inf); ThruSections supplies many line
    # pcurves for isoparametric edges of a BSpline surface). Trimming
    # to (u_first, u_last) also makes the resulting BSpline span
    # exactly the edge's segment rather than the parent curve's full
    # domain.
    try:
        if u_last > u_first:
            c2d = Geom2d_TrimmedCurve(c2d, u_first, u_last)
        bsp2d = geom2dconvert.CurveToBSplineCurve(c2d)
    except Exception as exc:  # noqa: BLE001 — defensive
        logger.debug("pcurve BSpline conversion failed: %s", exc)
        return None
    if bsp2d is None:
        return None

    poles_array = bsp2d.Poles()
    control_points: list[tuple[float, float]] = []
    for i in range(poles_array.Lower(), poles_array.Upper() + 1):
        p = poles_array.Value(i)
        control_points.append((float(p.X()), float(p.Y())))

    knots_array = bsp2d.Knots()
    knots = [float(knots_array.Value(i)) for i in range(knots_array.Lower(), knots_array.Upper() + 1)]

    mults_array = bsp2d.Multiplicities()
    mults = [int(mults_array.Value(i)) for i in range(mults_array.Lower(), mults_array.Upper() + 1)]

    weights: list[float] | None = None
    if bsp2d.IsRational():
        w_array = bsp2d.Weights()
        weights = [float(w_array.Value(i)) for i in range(w_array.Lower(), w_array.Upper() + 1)]

    return geo_cu.Pcurve2dBSpline(
        degree=int(bsp2d.Degree()),
        control_points_2d=control_points,
        knots=knots,
        knot_multiplicities=mults,
        weights=weights,
        closed=bool(bsp2d.IsClosed()),
    )


def get_wires_from_face(face: TopoDS_Face, surface: Geom_Surface) -> list[geo_cu.EdgeLoop]:
    """Walk the wires of an OCC face and emit one :class:`EdgeLoop` per
    wire.

    Returning the IFC-style ``EdgeLoop`` (with :class:`OrientedEdge`
    children carrying ``EdgeCurve`` geometry, start/end vertices, and
    orientation) means downstream consumers can wrap each loop in a
    :class:`FaceBound` and round-trip cleanly through
    :func:`ada.occ.geom.surfaces.make_face_from_geom` — the BSpline
    builder path expects exactly this chain.

    Each edge's 2D UV pcurve on the parent face is also extracted (when
    available); the OCC face builder needs it to keep the wire
    parameterised on a BSpline surface so the rebuilt face has the
    correct interior.
    """
    wire_explorer = TopExp_Explorer(face, TopAbs_WIRE)
    edge_loops: list[geo_cu.EdgeLoop] = []
    while wire_explorer.More():
        wire: TopoDS_Wire = topods.Wire(wire_explorer.Current())
        edge_loop = process_wire(wire, face, surface)
        if edge_loop is None or not edge_loop.edge_list:
            raise NotImplementedError("Failed to retrieve boundary curves from wire.")
        edge_loops.append(edge_loop)
        wire_explorer.Next()

    return edge_loops


def _edge_endpoints(edge: TopoDS_Edge) -> tuple[Point, Point]:
    """Read the 3D endpoints of an OCC edge from its first / last vertex."""
    v_first = topexp.FirstVertex(edge, True)
    v_last = topexp.LastVertex(edge, True)
    p_first = BRep_Tool.Pnt(v_first)
    p_last = BRep_Tool.Pnt(v_last)
    return (
        Point(p_first.X(), p_first.Y(), p_first.Z()),
        Point(p_last.X(), p_last.Y(), p_last.Z()),
    )


# Function to process the wire and retrieve the boundary curves
def process_wire(
    wire: TopoDS_Wire,
    face: TopoDS_Face,
    surface: Geom_Surface | None = None,
) -> geo_cu.EdgeLoop | None:
    """Build a proper :class:`EdgeLoop` from a TopoDS_Wire.

    Each OCC edge becomes an :class:`OrientedEdge` wrapping an
    :class:`EdgeCurve` (start point, end point, 3D curve geometry).
    The wire's traversal order is preserved so the loop reflects the
    OCC face's natural boundary direction; downstream
    ``make_face_from_geom`` reads the same order.

    ``face`` is needed in addition to ``surface`` because
    ``BRep_Tool.CurveOnSurface`` keys pcurves on the (edge, face)
    pair — there's no ``(edge, Geom_Surface)`` overload that recovers
    the OCC-attached pcurve. ``surface`` is kept on the signature
    for callers that still want explicit-surface semantics; we
    derive it from the face when omitted.
    """
    if surface is None:
        surface = BRep_Tool.Surface(face)
    oriented_edges: list[geo_cu.OrientedEdge] = []
    edge_explorer = TopExp_Explorer(wire, TopAbs_EDGE)
    while edge_explorer.More():
        edge = topods.Edge(edge_explorer.Current())

        # Analyze the edge geometry (e.g., BSpline curve, lines, etc.)
        edge_curve = BRep_Tool.Curve(edge)
        if edge_curve:
            curve_handle, first, last = BRep_Tool.Curve(edge)
            # print(f"Processing edge from {first} to {last}.")

            # Here you can check if the edge is a B-spline or other curve type
            # and process accordingly
            if curve_handle.DynamicType().Name() == "Geom_BSplineCurve":
                # Extract B-spline curve parameters
                bspline_curve = Geom_BSplineCurve.DownCast(curve_handle)

                degree = bspline_curve.Degree()
                poles = array1_to_point_list(bspline_curve.Poles())
                knots = array1_to_list(bspline_curve.Knots())
                mults = array1_to_int_list(bspline_curve.Multiplicities())
                closed = bool(bspline_curve.IsClosed())
                # There is no direct mapping for curve form / knot spec from OCC here; use UNSPECIFIED defaults
                curve_form = geo_cu.BSplineCurveFormEnum.UNSPECIFIED
                knot_spec = geo_cu.KnotType.UNSPECIFIED
                self_intersect = False

                if bspline_curve.IsRational():
                    weights = array1_to_list(bspline_curve.Weights())
                    curve = geo_cu.RationalBSplineCurveWithKnots(
                        degree=degree,
                        control_points_list=poles,
                        curve_form=curve_form,
                        closed_curve=closed,
                        self_intersect=self_intersect,
                        knot_multiplicities=mults,
                        knots=knots,
                        knot_spec=knot_spec,
                        weights_data=weights,
                    )
                else:
                    curve = geo_cu.BSplineCurveWithKnots(
                        degree=degree,
                        control_points_list=poles,
                        curve_form=curve_form,
                        closed_curve=closed,
                        self_intersect=self_intersect,
                        knot_multiplicities=mults,
                        knots=knots,
                        knot_spec=knot_spec,
                    )

            elif curve_handle.DynamicType().Name() == "Geom_Line":
                line_curve: Geom_Line = Geom_Line.DownCast(curve_handle)
                line_curve: Geom_Line

                # Process the line geometry
                line_pos = line_curve.Position()
                o: gp_Pnt = line_pos.Location()
                d: gp_Dir = line_pos.Direction()

                curve = geo_cu.Line(pnt=Point(o.X(), o.Y(), o.Z()), dir=Direction(d.X(), d.Y(), d.Z()))
            elif curve_handle.DynamicType().Name() == "Geom_Circle":
                circle = Geom_Circle.DownCast(curve_handle)
                pos = circle.Position()
                o: gp_Pnt = pos.Location()
                axis_dir: gp_Dir = pos.Direction()
                x_dir: gp_Dir = pos.XDirection()

                placement = Axis2Placement3D(
                    location=Point(o.X(), o.Y(), o.Z()),
                    axis=Direction(axis_dir.X(), axis_dir.Y(), axis_dir.Z()),
                    ref_direction=Direction(x_dir.X(), x_dir.Y(), x_dir.Z()),
                )
                curve = geo_cu.Circle(position=placement, radius=circle.Radius())
            elif curve_handle.DynamicType().Name() == "Geom_BezierCurve":
                # Bezier curves don't have a direct IFC representation,
                # but ``geomconvert.CurveToBSplineCurve`` produces an
                # exact BSpline form (degree-equal, single Bezier segment)
                # so downstream consumers see the same geometry.
                from OCC.Core.GeomConvert import geomconvert

                bsp = geomconvert.CurveToBSplineCurve(curve_handle)
                if bsp is None:
                    raise NotImplementedError("Failed to convert Geom_BezierCurve to BSpline form.")
                degree = bsp.Degree()
                poles = array1_to_point_list(bsp.Poles())
                knots = array1_to_list(bsp.Knots())
                mults = array1_to_int_list(bsp.Multiplicities())
                closed = bool(bsp.IsClosed())
                curve_form = geo_cu.BSplineCurveFormEnum.UNSPECIFIED
                knot_spec = geo_cu.KnotType.UNSPECIFIED
                if bsp.IsRational():
                    weights = array1_to_list(bsp.Weights())
                    curve = geo_cu.RationalBSplineCurveWithKnots(
                        degree=degree,
                        control_points_list=poles,
                        curve_form=curve_form,
                        closed_curve=closed,
                        self_intersect=False,
                        knot_multiplicities=mults,
                        knots=knots,
                        knot_spec=knot_spec,
                        weights_data=weights,
                    )
                else:
                    curve = geo_cu.BSplineCurveWithKnots(
                        degree=degree,
                        control_points_list=poles,
                        curve_form=curve_form,
                        closed_curve=closed,
                        self_intersect=False,
                        knot_multiplicities=mults,
                        knots=knots,
                        knot_spec=knot_spec,
                    )
            else:
                raise NotImplementedError(f"Edge geometry type {curve_handle.DynamicType().Name()} not implemented.")

            # Wrap the raw curve in an EdgeCurve + OrientedEdge so the
            # consumer (FaceBound → AdvancedFace → make_face_from_geom)
            # has the full IFC-style boundary chain to walk. Endpoints
            # come from the OCC vertex topology (matches the wire's
            # natural orientation regardless of curve parameterisation);
            # the orientation flag tells downstream whether the curve
            # should be traversed in its natural sense or reversed.
            #
            # We also extract the 2D UV pcurve on the parent surface —
            # the BSpline-surface face builder downstream uses it to
            # drive the OCC edge constructor with consistent surface
            # parametrisation. Without it, OCC re-projects 3D endpoints
            # onto the surface to recover UV, and for lofted ruled
            # BSpline surfaces that re-projection is ambiguous /
            # degenerate, leaving the bounding wire un-parameterised
            # on the surface so the rebuilt face ends up with zero
            # interior area.
            edge_start, edge_end = _edge_endpoints(edge)
            edge_curve_obj = geo_cu.EdgeCurve(
                start=edge_start,
                end=edge_end,
                edge_geometry=curve,
                same_sense=True,
            )
            pcurve = _extract_pcurve(edge, face)
            oriented_edges.append(
                geo_cu.OrientedEdge(
                    start=edge_start,
                    end=edge_end,
                    edge_element=edge_curve_obj,
                    orientation=(edge.Orientation() == TopAbs_FORWARD),
                    pcurve=pcurve,
                )
            )
        edge_explorer.Next()

    return geo_cu.EdgeLoop(edge_list=oriented_edges)
