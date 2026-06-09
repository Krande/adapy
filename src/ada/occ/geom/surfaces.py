import math

from OCC.Core.BRep import BRep_Builder, BRep_Tool
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCC.Core.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakeWire,
)
from OCC.Core.BRepTools import BRepTools_WireExplorer
from OCC.Core.Geom import (
    Geom_BSplineSurface,
    Geom_ConicalSurface,
    Geom_CylindricalSurface,
    Geom_SphericalSurface,
    Geom_ToroidalSurface,
)
from OCC.Core.Geom2d import Geom2d_BSplineCurve, Geom2d_Line, Geom2d_TrimmedCurve
from OCC.Core.Geom2dAPI import Geom2dAPI_PointsToBSpline
from OCC.Core.GeomAPI import GeomAPI_ProjectPointOnSurf
from OCC.Core.gp import (
    gp_Ax3,
    gp_Cone,
    gp_Cylinder,
    gp_Dir,
    gp_Dir2d,
    gp_Lin2d,
    gp_Pln,
    gp_Pnt,
    gp_Pnt2d,
    gp_Sphere,
    gp_Torus,
)
from OCC.Core.ShapeFix import ShapeFix_Face
from OCC.Core.TColgp import TColgp_Array1OfPnt2d, TColgp_Array2OfPnt
from OCC.Core.TColStd import (
    TColStd_Array1OfInteger,
    TColStd_Array1OfReal,
    TColStd_Array2OfReal,
)
from OCC.Core.TopAbs import TopAbs_FACE, TopAbs_REVERSED
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopLoc import TopLoc_Location
from OCC.Core.TopoDS import TopoDS_Compound, TopoDS_Face, TopoDS_Shape, TopoDS_Shell

from ada.config import Config, logger
from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su
from ada.geom.curves import PolyLoop
from ada.geom.surfaces import FaceBasedSurfaceModel
from ada.occ.exceptions import (  # noqa: F401 — used by lockstep wire build
    UnableToCreateCurveOCCGeom,
    UnableToCreateTesselationFromSolidOCCGeom,
)
from ada.occ.geom.curves import (
    make_edge_from_edge,
    make_wire_from_circle,
    make_wire_from_curve,
    make_wire_from_edge_loop,
    make_wire_from_face_bound,
    make_wire_from_indexed_poly_curve_geom,
    make_wire_from_poly_loop,
)
from ada.occ.utils import point3d, transform_shape_to_pos


def make_face_from_poly_loop(poly_loop: PolyLoop) -> TopoDS_Shape:
    wire = make_wire_from_poly_loop(poly_loop)
    return BRepBuilderAPI_MakeFace(wire).Shape()


def make_face_from_indexed_poly_curve_geom(curve: geo_cu.IndexedPolyCurve) -> TopoDS_Shape:
    wire = make_wire_from_indexed_poly_curve_geom(curve)
    return BRepBuilderAPI_MakeFace(wire).Shape()


def make_face_from_circle(circle: geo_cu.Circle):
    if circle.radius <= 0:
        from ada.config import logger

        logger.error(f"make_face_from_circle: bad radius={circle.radius} at {circle.position.location}")
    circle_wire = make_wire_from_circle(circle)
    return BRepBuilderAPI_MakeFace(circle_wire).Shape()


def make_shell_from_face_based_surface_geom(surface: FaceBasedSurfaceModel) -> TopoDS_Shape:
    occ_face = None
    for face in surface.fbsm_faces:
        for cfs_face in face.cfs_faces:
            if not isinstance(cfs_face.bound, PolyLoop):
                raise NotImplementedError("Only PolyLoop bounds are supported")
            new_face = make_face_from_poly_loop(cfs_face.bound)
            if occ_face is None:
                occ_face = new_face
            else:
                # Fuse the new face with the existing face
                occ_face = BRepAlgoAPI_Fuse(new_face, occ_face).Shape()

    return occ_face


def make_shell_from_curve_bounded_plane_geom(surface: geo_su.CurveBoundedPlane) -> TopoDS_Shape:
    if isinstance(surface.outer_boundary, geo_cu.IndexedPolyCurve):
        outer_curve = surface.outer_boundary
        face = make_face_from_indexed_poly_curve_geom(outer_curve)
        for inner_curve in map(make_face_from_curve, surface.inner_boundaries):
            face = BRepAlgoAPI_Cut(face, inner_curve).Shape()
    else:
        raise NotImplementedError(f"Curve type {type(surface.outer_boundary)} not implemented")

    position = surface.basis_surface.position
    face = transform_shape_to_pos(face, position.location, position.axis, position.ref_direction)

    return face


def make_bspline_surface_with_knots(
    advanced_face: geo_su.BSplineSurfaceWithKnots | geo_su.RationalBSplineSurfaceWithKnots,
) -> Geom_BSplineSurface:
    # Define control points
    num_u = advanced_face.get_num_u_control_points()
    num_v = advanced_face.get_num_v_control_points()
    control_points = TColgp_Array2OfPnt(1, num_u, 1, num_v)

    # Fill control points grid
    for u in range(0, num_u):
        for v in range(0, num_v):
            val = advanced_face.control_points_list[u][v]
            control_points.SetValue(u + 1, v + 1, gp_Pnt(val.x, val.y, val.z))

    # Set degrees (order = degree + 1)
    degree_u = advanced_face.u_degree
    degree_v = advanced_face.v_degree

    num_u_knots = len(advanced_face.u_knots)
    num_v_knots = len(advanced_face.v_knots)

    # Define knots for U direction
    knots_u = TColStd_Array1OfReal(1, num_u_knots)
    for i, knot in enumerate(advanced_face.u_knots, start=1):
        knots_u.SetValue(i, knot)

    # Define multiplicities for U direction
    multiplicities_u = TColStd_Array1OfInteger(1, num_u_knots)
    for i, mult in enumerate(advanced_face.u_multiplicities, start=1):
        multiplicities_u.SetValue(i, mult)

    # Define knots for V direction
    knots_v = TColStd_Array1OfReal(1, num_v_knots)
    for i, knot in enumerate(advanced_face.v_knots, start=1):
        knots_v.SetValue(i, knot)

    # Define multiplicities for V direction
    multiplicities_v = TColStd_Array1OfInteger(1, num_v_knots)
    for i, mult in enumerate(advanced_face.v_multiplicities, start=1):
        multiplicities_v.SetValue(i, mult)

    if Config().general_debug:
        # print the contents of each array
        logger.debug("Control points:")
        for u in range(1, num_u + 1):
            for v in range(1, num_v + 1):
                logger.debug(
                    control_points.Value(u, v).X(), control_points.Value(u, v).Y(), control_points.Value(u, v).Z()
                )
        logger.debug("Knots in U direction:")
        for i in range(1, num_u_knots + 1):
            logger.debug(knots_u.Value(i))
        logger.debug("Multiplicities in U direction:")
        for i in range(1, num_u_knots + 1):
            logger.debug(multiplicities_u.Value(i))
        logger.debug("Knots in V direction:")
        for i in range(1, num_v_knots + 1):
            logger.debug(knots_v.Value(i))
        logger.debug("Multiplicities in V direction:")
        for i in range(1, num_v_knots + 1):
            logger.debug(multiplicities_v.Value(i))
    if type(advanced_face) is geo_su.RationalBSplineSurfaceWithKnots:
        # Define weights
        weights_data = advanced_face.weights_data
        num_weights = len(weights_data)
        # **Define weights**
        weights = TColStd_Array2OfReal(1, num_u, 1, num_v)
        # Fill weights grid
        for u in range(0, num_u):
            for v in range(0, num_v):
                weight = weights_data[u][v]
                weights.SetValue(u + 1, v + 1, weight)

        if Config().general_debug:
            logger.debug("Weights:")
            for i in range(1, num_weights + 1):
                logger.debug(weights.Value(i))

        # Create the B-Spline surface
        bspline_surface = Geom_BSplineSurface(
            control_points,  # Control points
            weights,  # Weights
            knots_u,  # Knots in U direction
            knots_v,  # Knots in V direction
            multiplicities_u,  # Multiplicities in U direction
            multiplicities_v,  # Multiplicities in V direction
            degree_u,  # Degree in U direction
            degree_v,  # Degree in V direction
            False,  # Is the surface periodic in U direction
            False,  # Is the surface periodic in V direction
        )
    else:
        # Create the B-Spline surface
        bspline_surface = Geom_BSplineSurface(
            control_points,  # Control points
            knots_u,  # Knots in U direction
            knots_v,  # Knots in V direction
            multiplicities_u,  # Multiplicities in U direction
            multiplicities_v,  # Multiplicities in V direction
            degree_u,  # Degree in U direction
            degree_v,  # Degree in V direction
            False,  # Is the surface periodic in U direction
            False,  # Is the surface periodic in V direction
        )

    return bspline_surface


def update_edges_4corners(edges, builder, face_surface):
    # Create corresponding 2D curves in the parametric space (u-v space) of the B-Spline surface
    uv1 = gp_Pnt2d(0.0, 0.0)
    uv2 = gp_Pnt2d(0.0, 1.0)
    uv3 = gp_Pnt2d(1.0, 1.0)
    uv4 = gp_Pnt2d(1.0, 0.0)

    c2d_edges = [
        Geom2d_TrimmedCurve(Geom2d_Line(gp_Lin2d(uv1, gp_Dir2d(uv2.XY() - uv1.XY()))), 0.0, 1.0),
        Geom2d_TrimmedCurve(Geom2d_Line(gp_Lin2d(uv2, gp_Dir2d(uv3.XY() - uv2.XY()))), 0.0, 1.0),
        Geom2d_TrimmedCurve(Geom2d_Line(gp_Lin2d(uv3, gp_Dir2d(uv4.XY() - uv3.XY()))), 0.0, 1.0),
        Geom2d_TrimmedCurve(Geom2d_Line(gp_Lin2d(uv4, gp_Dir2d(uv1.XY() - uv4.XY()))), 0.0, 1.0),
    ]

    # Assign the 2D curves to the edges on the B-Spline surface using the correct signature
    identity_location = TopLoc_Location()  # No transformation (identity)
    for i, edge in enumerate(edges):
        builder.UpdateEdge(edge, c2d_edges[i], face_surface, identity_location, 1e-6)


_pcurve_probe_count = 0


def _build_geom2d_bspline(pcurve_geom):
    """Construct a Geom2d_BSplineCurve from a Pcurve2dBSpline dataclass.

    Returns None on construction failure; the caller can fall back to
    the regenerative path."""
    cps = pcurve_geom.control_points_2d
    knots = list(pcurve_geom.knots)
    mults = list(pcurve_geom.knot_multiplicities)
    if not cps or not knots or len(knots) != len(mults):
        return None
    n_poles = len(cps)
    poles = TColgp_Array1OfPnt2d(1, n_poles)
    for i, cp in enumerate(cps, start=1):
        poles.SetValue(i, gp_Pnt2d(float(cp[0]), float(cp[1])))
    knots_arr = TColStd_Array1OfReal(1, len(knots))
    mults_arr = TColStd_Array1OfInteger(1, len(mults))
    for i, (k, m) in enumerate(zip(knots, mults), start=1):
        knots_arr.SetValue(i, float(k))
        mults_arr.SetValue(i, int(m))
    try:
        if pcurve_geom.weights:
            weights_arr = TColStd_Array1OfReal(1, len(pcurve_geom.weights))
            for i, w in enumerate(pcurve_geom.weights, start=1):
                weights_arr.SetValue(i, float(w))
            return Geom2d_BSplineCurve(
                poles,
                weights_arr,
                knots_arr,
                mults_arr,
                int(pcurve_geom.degree),
                bool(pcurve_geom.closed),
            )
        return Geom2d_BSplineCurve(
            poles,
            knots_arr,
            mults_arr,
            int(pcurve_geom.degree),
            bool(pcurve_geom.closed),
        )
    except Exception as ex:
        logger.warning(f"Geom2d_BSplineCurve construction failed: {ex}")
        return None


def _make_edge_from_pcurve(pcurve_geom, face_surface):
    """Build an OCC edge from a 2D BSpline pcurve + the face's surface.

    The 3D parametrization is derived implicitly by OCCT from
    surface(pcurve(t)), so 2D and 3D are guaranteed-consistent.
    Returns None on any failure so the caller falls back to building
    from the SAT-supplied 3D BSpline + reparam.
    """
    c2d = _build_geom2d_bspline(pcurve_geom)
    if c2d is None:
        return None
    try:
        first = float(c2d.FirstParameter())
        last = float(c2d.LastParameter())
        maker = BRepBuilderAPI_MakeEdge(c2d, face_surface, first, last)
        if not maker.IsDone():
            return None
        return maker.Edge()
    except Exception as ex:
        logger.warning(f"BRepBuilderAPI_MakeEdge(c2d, surface) failed: {ex}")
        return None


def _attach_supplied_pcurve(builder, edge, pcurve_geom, face_surface, identity_location) -> bool:
    """Attach a SAT-supplied 2D BSpline pcurve to ``edge`` on ``face_surface``.

    Returns True on success, False on any structural problem (in which
    case the caller should fall back to the regenerative path).

    KNOWN BUG — pcurve trim, not affine remap (2026-05-03):
    The current code AFFINELY REMAPS the pcurve's knot range onto the
    OCC edge's 3D parameter range. That's wrong when the SAT pcurve
    covers more of its 2D curve than the edge actually uses (which is
    common — multi-edge wires on a single UV side share the underlying
    UV trajectory, with each edge picking a sub-range).

    Symptom: face has 0 m² area, BRepMesh produces 2-3 degenerate
    triangles, plate appears as a hole. Reproduced on plate-shaped
    faces with a 6-edge wire — two short (0.4 m) and two long (2.7 m)
    vertical segments along the plate's right and left UV sides. The
    vertical pcurves all carry CPs at the surface's full v-extent
    (-3.1, 0) even when their edge only spans 0.4 m or 2.7 m of it.
    Affine remap stretches the FULL pcurve onto each edge's parameter
    range, so all four vertical edges trace the entire UV side — the
    resulting wire self-intersects in UV and encloses zero area.

    Per ACIS SAT v4.0 spec (Chapter 6 "pcurve type", page 6-61):
    "a parameter-space curve must always have the same parameter range
    as its associated object-space curve, and its internal
    parameterization must be similar". The fix is to TRIM the pcurve to
    the edge's t-range (not remap), with the trim points found by
    mapping the SAT edge's parameters into the pcurve's parameter
    space. ``OrientedEdge.t_start`` / ``t_end`` (threaded in 57b9ad48)
    carry the SAT-recorded edge parameters; the pcurve's knot range is
    its native [s_min, s_max]. Some pcurves additionally run in the
    OPPOSITE direction to the 3D curve — detectable by evaluating each
    pcurve endpoint through the surface and comparing to the 3D curve's
    endpoints, then reversing the trim if they disagree.

    Implementation outline for the next session:
      1. Compute t→s mapping (forward or reversed) by checking which
         pcurve endpoint matches which 3D-curve endpoint (via
         surface(pcurve(s_first)) ≈ 3D-curve(t_first)).
      2. Map [edge.t_start, edge.t_end] → [s_a, s_b] in pcurve space.
      3. Build ``Geom2d_TrimmedCurve(c2d, min(s_a, s_b), max(s_a, s_b),
         sense=...)`` and use that as the attached pcurve, OR
         re-knot/re-CP a fresh ``Geom2d_BSplineCurve`` covering exactly
         that sub-range.
      4. Optionally use ``BRepBuilderAPI_MakeEdge(c2d, surface, t1, t2)``
         to build the OCC edge directly from the trimmed pcurve so the
         3D parameterisation is derived as ``surface(c2d(t))`` and is
         guaranteed-consistent.

    Affected env knobs: ``ADA_USE_SAT_PCURVES`` (skip pcurves entirely
    — falls back to OCC's reproject-and-fit, which on this dataset
    produces NEGATIVE surface area, so the issue isn't purely the
    affine remap — the wire 3D-projection itself has a direction
    problem that ``ADA_PCURVE_REVERSE`` may also need to address).
    """
    # Debug: print UV bounds for the first few attaches so we can spot
    # ACIS↔OCCT domain mismatches. Toggle via ADA_PCURVE_PROBE=N.
    import os as _os

    global _pcurve_probe_count
    probe_n = int(_os.environ.get("ADA_PCURVE_PROBE") or 0)
    cps = pcurve_geom.control_points_2d
    knots = pcurve_geom.knots
    mults = pcurve_geom.knot_multiplicities
    if not cps or not knots or len(knots) != len(mults):
        return False

    # OCCT requires the 2D pcurve parameter range to match the OCC
    # edge's 3D parameter range (the SameRange / SameParameter flags
    # default to True after BRepBuilderAPI_MakeEdge). ACIS pcurves
    # carry their own knot range, totally unrelated to whatever range
    # OCCT picked for the 3D edge — we *must* affinely remap our knots
    # to the edge's [first, last] before the attach, otherwise OCCT
    # silently evaluates the 2D curve at the wrong parameters and the
    # face lands somewhere else on the surface.
    try:
        edge_curve_handle, edge_first, edge_last = BRep_Tool.Curve(edge)
    except Exception:
        edge_curve_handle = None
        edge_first = edge_last = 0.0
    pcurve_first = float(knots[0])
    pcurve_last = float(knots[-1])
    pcurve_span = pcurve_last - pcurve_first
    edge_span = float(edge_last) - float(edge_first)
    reparam_applied = False
    if (
        edge_curve_handle is not None
        and pcurve_span > 0.0
        and edge_span > 0.0
        and (abs(pcurve_first - edge_first) > 1e-9 or abs(pcurve_last - edge_last) > 1e-9)
    ):
        scale = edge_span / pcurve_span
        knots = [float(edge_first) + (float(k) - pcurve_first) * scale for k in knots]
        reparam_applied = True
    if probe_n > 0 and _pcurve_probe_count < probe_n:
        logger.warning(
            "[pcurve probe %d edge_param] edge=[%.4f,%.4f] pcurve=[%.4f,%.4f] reparam=%s",
            _pcurve_probe_count,
            float(edge_first),
            float(edge_last),
            pcurve_first,
            pcurve_last,
            reparam_applied,
        )

    n_poles = len(cps)
    poles = TColgp_Array1OfPnt2d(1, n_poles)
    for i, cp in enumerate(cps, start=1):
        poles.SetValue(i, gp_Pnt2d(float(cp[0]), float(cp[1])))
    knots_arr = TColStd_Array1OfReal(1, len(knots))
    mults_arr = TColStd_Array1OfInteger(1, len(mults))
    for i, (k, m) in enumerate(zip(knots, mults), start=1):
        knots_arr.SetValue(i, float(k))
        mults_arr.SetValue(i, int(m))
    try:
        if pcurve_geom.weights:
            weights_arr = TColStd_Array1OfReal(1, len(pcurve_geom.weights))
            for i, w in enumerate(pcurve_geom.weights, start=1):
                weights_arr.SetValue(i, float(w))
            c2d = Geom2d_BSplineCurve(
                poles, weights_arr, knots_arr, mults_arr, int(pcurve_geom.degree), bool(pcurve_geom.closed)
            )
        else:
            c2d = Geom2d_BSplineCurve(poles, knots_arr, mults_arr, int(pcurve_geom.degree), bool(pcurve_geom.closed))
    except Exception as ex:
        logger.warning(f"supplied SAT pcurve failed Geom2d_BSplineCurve construction: {ex}")
        return False
    # Sanity-check the constructed curve at endpoints — same defence the
    # regenerative path uses.
    try:
        first_p = c2d.Value(c2d.FirstParameter())
        last_p = c2d.Value(c2d.LastParameter())
    except Exception:
        return False
    for s in (first_p, last_p):
        if not (math.isfinite(s.X()) and math.isfinite(s.Y())):
            return False
    if probe_n > 0 and _pcurve_probe_count < probe_n:
        try:
            u0, u1, v0, v1 = face_surface.Bounds()
        except Exception:
            u0 = u1 = v0 = v1 = float("nan")
        first_param = c2d.FirstParameter()
        last_param = c2d.LastParameter()
        # Sample 3 pcurve points; print 2D and corresponding 3D via
        # face_surface.Value(u, v).
        samples = []
        for t in (first_param, (first_param + last_param) * 0.5, last_param):
            try:
                p2 = c2d.Value(t)
                p3 = face_surface.Value(p2.X(), p2.Y())
                samples.append((t, (p2.X(), p2.Y()), (p3.X(), p3.Y(), p3.Z())))
            except Exception:
                samples.append((t, None, None))
        logger.warning(
            "[pcurve probe %d] surface_uv=[%.4f,%.4f]x[%.4f,%.4f] pcurve_param=[%.4f,%.4f] cp_uv_first=%s cp_uv_last=%s samples=%s",
            _pcurve_probe_count,
            u0,
            u1,
            v0,
            v1,
            first_param,
            last_param,
            tuple(cps[0]),
            tuple(cps[-1]),
            samples,
        )
        _pcurve_probe_count += 1
    builder.UpdateEdge(edge, c2d, face_surface, identity_location, 1e-6)
    return True


def update_edges_uv_gen(edges, builder, face_surface, supplied_pcurves=None) -> tuple[int, int]:
    """Attach UV-space (p-curve) BSpline curves to each edge of a wire
    on a Geom_BSplineSurface. Returns (n_updated, n_total).

    When ``supplied_pcurves`` is provided (one entry per edge, ``None``
    where unavailable), each non-None pcurve is attached directly via
    ``BRep_Builder.UpdateEdge`` — this is the SAT-authored UV curve, no
    reprojection. Edges without a supplied pcurve fall through to the
    regenerative sampling path, which is fragile and gated by several
    sanity checks to avoid OCCT heap corruption (``double free`` /
    ``Knots interval values too close``).

    Edges where the construction fails are left without a p-curve; the
    caller (``make_face_from_geom``) treats any incomplete update as a
    degenerate face and skips it.
    """
    identity_location = TopLoc_Location()  # No transformation (identity)
    n_total = 0
    n_updated = 0
    for idx, edge in enumerate(edges):
        # Edges where ``supplied_pcurves[idx] is None`` were built by
        # ``_make_edge_from_pcurve`` (drive_edge path) — the OCC edge
        # already carries a consistent 2D pcurve from the
        # ``BRepBuilderAPI_MakeEdge(c2d, surface, t1, t2)`` constructor.
        # Don't re-process them: regen would (a) overwrite the
        # authored pcurve with a noisier one or (b) fail one of the
        # safety pre-screens and be counted as a failed update,
        # falsely tripping the strict ``n_updated < n_total`` guard
        # in ``_build_bspline_wire``. They're not in scope for this
        # function's "make sure every edge has a pcurve" purpose, so
        # skip them entirely (don't count toward n_total either).
        if supplied_pcurves is not None and idx < len(supplied_pcurves):
            if supplied_pcurves[idx] is None:
                continue
        n_total += 1
        # Fast path: the file already gave us the UV curve for this coedge.
        if supplied_pcurves is not None and idx < len(supplied_pcurves):
            supplied = supplied_pcurves[idx]
            if supplied is not None:
                if _attach_supplied_pcurve(builder, edge, supplied, face_surface, identity_location):
                    n_updated += 1
                    continue
                # If the supplied pcurve was structurally bad, fall through to regen.
        try:
            # Get the 3D curve of the edge
            edge_curve_handle, first, last = BRep_Tool.Curve(edge)
            if edge_curve_handle is None:
                continue

            # Sample points along the edge
            num_samples = 30
            parameters = [first + (last - first) * i / (num_samples - 1) for i in range(num_samples)]
            points_3d = [edge_curve_handle.Value(u) for u in parameters]
            # Project points onto the surface to get (u,v) parameters
            array_2d_points = TColgp_Array1OfPnt2d(1, num_samples)
            projection_failed = False
            for i, pt in enumerate(points_3d):
                projector = GeomAPI_ProjectPointOnSurf(pt, face_surface)
                if projector.NbPoints() == 0:
                    projection_failed = True
                    break
                u, v = projector.LowerDistanceParameters()
                if not (math.isfinite(u) and math.isfinite(v)):
                    projection_failed = True
                    break
                array_2d_points.SetValue(i + 1, gp_Pnt2d(u, v))
            if projection_failed:
                logger.warning("Failed to project edge sample onto BSpline surface; skipping p-curve update")
                continue
            # Pre-screen inputs before handing to Geom2dAPI_PointsToBSpline.
            # The constructor with chord-length parametrisation panics on
            # near-duplicate samples and the failure path is what corrupts
            # the OCCT heap (observed: ``double free or corruption`` even
            # when we discarded the output curve). Refusing the result
            # downstream is too late — we must avoid the call itself when
            # the inputs would trigger ``Knots interval values too close``.
            CHORD_TOL = 1.0e-9
            UV_INPUT_LIMIT = 1.0e6
            uv_pts = [array_2d_points.Value(i + 1) for i in range(num_samples)]
            # Reject huge UV magnitudes — these mean the projection landed on
            # an unbounded extension of the surface and the BSpline build
            # multiplies the magnitude further before reporting failure.
            if any(abs(p.X()) > UV_INPUT_LIMIT or abs(p.Y()) > UV_INPUT_LIMIT for p in uv_pts):
                logger.warning("UV samples grossly out of range; skipping p-curve update")
                continue
            chord_total = 0.0
            min_chord = float("inf")
            for i in range(1, num_samples):
                a, b = uv_pts[i - 1], uv_pts[i]
                d = math.hypot(b.X() - a.X(), b.Y() - a.Y())
                chord_total += d
                if d < min_chord:
                    min_chord = d
            if not math.isfinite(chord_total) or chord_total < CHORD_TOL or min_chord < CHORD_TOL:
                logger.warning(
                    "UV samples are near-duplicates "
                    f"(chord_total={chord_total:.3e}, min_chord={min_chord:.3e}); "
                    "skipping p-curve update to avoid Geom2dAPI_PointsToBSpline heap corruption"
                )
                continue
            # Build a Geom2d_BSplineCurve from the (u,v) points
            # 3rd param is Approx_ChordLength (1) or Approx_Centripetal (2) or Approx_IsoParametric (3)
            # We use default (Approx_ChordLength) which is usually fine for ordered points
            interpolator = Geom2dAPI_PointsToBSpline(array_2d_points)
            if not interpolator.IsDone():
                logger.warning(f"Failed to create 2D BSpline for edge {edge}")
                continue

            c2d_edge = interpolator.Curve()
            # Sanity-check the constructed curve. IsDone() can return True
            # for garbage geometry (degenerate inputs producing a curve
            # whose Value() returns 1e+13-magnitude points). Such curves
            # crash BRepBuilderAPI_MakeFace / ShapeFix later. Sample three
            # parameters (start, mid, end) and reject anything non-finite
            # or grossly out of range — UV is normally [0, 1]^2.
            UV_SANITY_LIMIT = 1.0e6
            try:
                c2d_first = c2d_edge.FirstParameter()
                c2d_last = c2d_edge.LastParameter()
                samples = (
                    c2d_edge.Value(c2d_first),
                    c2d_edge.Value((c2d_first + c2d_last) * 0.5),
                    c2d_edge.Value(c2d_last),
                )
            except Exception:
                logger.warning("constructed p-curve threw on Value(); treating as failed")
                continue
            bad_curve = False
            for s in samples:
                if not (math.isfinite(s.X()) and math.isfinite(s.Y())):
                    bad_curve = True
                    break
                if abs(s.X()) > UV_SANITY_LIMIT or abs(s.Y()) > UV_SANITY_LIMIT:
                    bad_curve = True
                    break
            if bad_curve:
                logger.warning("constructed p-curve produced out-of-range UV; treating as failed")
                continue
            # Now assign the 2D curve to the edge
            builder.UpdateEdge(edge, c2d_edge, face_surface, identity_location, 1e-6)
            n_updated += 1
        except Exception as ex:
            # Standard_ConstructionError ("Knots interval values too close")
            # comes through here. Leaving the edge un-updated is OK; the
            # caller raises if any edge failed so the whole face is
            # skipped rather than passed half-built into MakeFace.
            logger.warning(f"Error updating edge p-curve: {ex}")
    return n_updated, n_total


def _curve_on_surface(edge, face_surface):
    """Wrap BRep_Tool.CurveOnSurface so we tolerate both pythonocc return
    shapes seen in the wild: ``[curve, first, last]`` (3-tuple, common)
    and ``[first, last]`` (2-tuple, where the curve handle is omitted on
    NULL — observed on the prod worker's pythonocc build). Returns
    ``(curve_or_None, first, last)`` and never raises ValueError on
    unpack."""
    result = BRep_Tool.CurveOnSurface(edge, face_surface, TopLoc_Location())
    if not isinstance(result, (list, tuple)):
        return None, 0.0, 0.0
    if len(result) >= 3:
        # Standard shape; ignore any trailing optional fields.
        return result[0], float(result[1]), float(result[2])
    if len(result) == 2:
        a, b = result
        # Some pythonocc builds collapse the NULL curve handle and
        # return just the parameter range. Recognise that as "no
        # p-curve here" and let the caller skip the edge.
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            return None, float(a), float(b)
        # Other rarer shape: (curve, (first, last)).
        if isinstance(b, (list, tuple)) and len(b) == 2:
            return a, float(b[0]), float(b[1])
    return None, 0.0, 0.0


def is_wire_cw(wire, face_surface):
    # Calculate signed area of the polygon formed by edge endpoints in UV space.
    # Raises UnableToCreateTesselationFromSolidOCCGeom when any p-curve sample
    # is non-finite or the resulting area is unreasonable — these are the
    # tells that an upstream Geom2dAPI_PointsToBSpline produced garbage and
    # feeding the wire into MakeFace/ShapeFix would corrupt the OCCT heap
    # (observed crash: ``double free or corruption`` mid-tessellation).
    AREA_SANITY_LIMIT = 1.0e10  # UV is normally in [0, 1]^2 — anything past this is garbage.
    area = 0.0
    exp = (
        BRepTools_WireExplorer(wire, face_surface)
        if isinstance(face_surface, TopoDS_Face)
        else BRepTools_WireExplorer(wire)
    )
    # WireExplorer iterates edges in wire order
    while exp.More():
        edge = exp.Current()
        # Get p-curve
        curve, first, last = _curve_on_surface(edge, face_surface)
        if curve:
            # Note: WireExplorer handles orientation. If edge is REVERSED in wire,
            # it still returns the edge with REVERSED orientation.
            # However, we need UV coords following the LOOP direction.

            p1 = curve.Value(first)
            p2 = curve.Value(last)

            for pt in (p1, p2):
                if not (math.isfinite(pt.X()) and math.isfinite(pt.Y())):
                    raise UnableToCreateTesselationFromSolidOCCGeom(
                        "BSpline-surface p-curve returned non-finite UV — degenerate face, skipping."
                    )

            # Handle orientation
            if edge.Orientation() == TopAbs_REVERSED:
                # Wire goes Last -> First
                uv_start = p2
                uv_end = p1
            else:
                # Wire goes First -> Last
                uv_start = p1
                uv_end = p2

            # Integration term (Shoelace formula)
            # Sum (x2 - x1) * (y2 + y1)
            area += (uv_end.X() - uv_start.X()) * (uv_end.Y() + uv_start.Y())

        exp.Next()

    if not math.isfinite(area) or abs(area) > AREA_SANITY_LIMIT:
        raise UnableToCreateTesselationFromSolidOCCGeom(
            f"Wire signed area {area!r} is non-finite or out of range — degenerate face, skipping."
        )

    # Area > 0 means CW (for standard UV coords where U=Right, V=Up)
    # Area < 0 means CCW
    logger.debug(f"Wire signed area: {area}")
    return area > 0


def create_wire_from_bounds(bounds, face_surface, builder: BRep_Builder):
    edges = []
    for edge_loop in bounds:
        for para_edge in edge_loop.bound.edge_list:
            occ_edge = BRepBuilderAPI_MakeEdge(point3d(para_edge.start), point3d(para_edge.end)).Edge()
            edges.append(occ_edge)

    n_updated, n_total = update_edges_uv_gen(edges, builder, face_surface)
    if n_updated < n_total:
        raise UnableToCreateTesselationFromSolidOCCGeom(
            f"create_wire_from_bounds: p-curve update incomplete ({n_updated}/{n_total})"
        )

    # if len(edges) == 4:
    #     update_edges_4corners(edges, builder, face_surface)
    # else:
    #     update_edges_uv_gen(edges, builder, face_surface)

    wire_maker = BRepBuilderAPI_MakeWire()
    for edge in edges:
        wire_maker.Add(edge)

    return wire_maker.Wire()


def _points_close(a, b, tol: float = 1e-6) -> bool:
    try:
        return all(abs(float(x) - float(y)) <= tol for x, y in zip(a, b))
    except Exception:
        return False


def _has_full_circle_edge(advanced_face: geo_su.AdvancedFace) -> bool:
    """True if any boundary edge is a full circle/ellipse (start == end) — the
    signature of a face that is *closed* in a parametric direction (a full cylinder /
    cone / torus tube), which OCC represents with a seam, not a simple wire."""
    for fb in advanced_face.bounds:
        for oe in getattr(getattr(fb, "bound", None), "edge_list", []):
            ec = getattr(oe, "edge_element", None)
            geom = getattr(ec, "edge_geometry", None)
            if isinstance(geom, (geo_cu.Circle, geo_cu.Ellipse)) and _points_close(oe.start, oe.end):
                return True
    return False


def _sample_edge_points(oe):
    """World-space sample points along one oriented edge. A full circle (start==end)
    is sampled all the way round so the projected parameter range captures the full
    closed direction; other edges contribute their endpoints."""
    import math

    pts = [
        (float(oe.start[0]), float(oe.start[1]), float(oe.start[2])),
        (float(oe.end[0]), float(oe.end[1]), float(oe.end[2])),
    ]
    ec = getattr(oe, "edge_element", oe)
    g = getattr(ec, "edge_geometry", None)
    if isinstance(g, geo_cu.Circle) and _points_close(oe.start, oe.end):
        pos = g.position
        c = [float(x) for x in pos.location]
        z = [float(x) for x in pos.axis]
        xd = [float(x) for x in pos.ref_direction]
        yd = (z[1] * xd[2] - z[2] * xd[1], z[2] * xd[0] - z[0] * xd[2], z[0] * xd[1] - z[1] * xd[0])
        r = float(g.radius)
        for k in range(12):
            a = 2.0 * math.pi * k / 12.0
            ca, sa = math.cos(a), math.sin(a)
            pts.append(
                (
                    c[0] + r * (ca * xd[0] + sa * yd[0]),
                    c[1] + r * (ca * xd[1] + sa * yd[1]),
                    c[2] + r * (ca * xd[2] + sa * yd[2]),
                )
            )
    return pts


def _param_extent(vals: list[float], periodic: bool, period: float) -> tuple[float, float]:
    """Bounding parameter interval for ``vals``. For a periodic direction: if the
    samples cover (nearly) the whole period the face is closed → return [0, period];
    otherwise the face spans the arc on the far side of the largest gap (handling the
    seam wraparound)."""
    lo, hi = min(vals), max(vals)
    if not periodic or (hi - lo) < 1e-9:
        return lo, hi
    s = sorted(vals)
    best_gap = (s[0] + period) - s[-1]  # the wraparound gap
    best_i = -1  # -1 == the gap is at the seam
    for i in range(len(s) - 1):
        gap = s[i + 1] - s[i]
        if gap > best_gap:
            best_gap, best_i = gap, i
    if best_gap < 0.15 * period:
        return 0.0, period  # samples cover the whole period → closed in this direction
    if best_i == -1:
        return s[0], s[-1]
    return s[best_i + 1], s[best_i] + period


def _try_make_closed_revolution_face(advanced_face: geo_su.AdvancedFace, face_surface):
    """Build a face that is *closed* in a parametric direction (full cylinder / cone /
    torus tube — signalled by a full-circle boundary edge) directly from the surface's
    parametric bounds, so OCC generates the seam itself. ``make_wire_from_face_bound``
    can't build a wire out of full-circle edges plus a doubled seam, which is why these
    faces (~5% of real-CAD curved faces — pipe walls, elbows) otherwise drop. Returns a
    face or None."""
    import math

    from OCC.Core.Geom import (
        Geom_ConicalSurface,
        Geom_CylindricalSurface,
        Geom_ToroidalSurface,
    )

    if not isinstance(face_surface, (Geom_CylindricalSurface, Geom_ConicalSurface, Geom_ToroidalSurface)):
        return None
    if not _has_full_circle_edge(advanced_face):
        return None

    # Recover (u, v) parameter ranges by projecting the boundary samples onto the
    # surface; the closed direction(s) snap to the full period via _param_extent.
    us: list[float] = []
    vs: list[float] = []
    for fb in advanced_face.bounds:
        for oe in getattr(getattr(fb, "bound", None), "edge_list", []):
            for p in _sample_edge_points(oe):
                proj = GeomAPI_ProjectPointOnSurf(gp_Pnt(*p), face_surface)
                if proj.NbPoints() > 0:
                    u, v = proj.LowerDistanceParameters()
                    us.append(u)
                    vs.append(v)
    if len(us) < 3:
        return None

    two_pi = 2.0 * math.pi
    umin, umax = _param_extent(us, bool(face_surface.IsUPeriodic()), two_pi)
    vmin, vmax = _param_extent(vs, bool(face_surface.IsVPeriodic()), two_pi)
    if (umax - umin) < 1e-9 or (vmax - vmin) < 1e-9:
        return None

    mk = BRepBuilderAPI_MakeFace(face_surface, umin, umax, vmin, vmax, 1e-6)
    if not mk.IsDone():
        return None
    return mk.Face()


def make_face_from_geom(advanced_face: geo_su.AdvancedFace) -> TopoDS_Face:
    """Create an OCC face from an AdvancedFace with arbitrary supported surface types and bounds.

    Supports Plane, CylindricalSurface, ConicalSurface, SphericalSurface, ToroidalSurface,
    BSplineSurfaceWithKnots and RationalBSplineSurfaceWithKnots.
    """
    # Build the OCC surface from the adapy face surface
    face_surface = make_surface_from_geom(advanced_face.face_surface)

    # A fully-closed periodic face (a complete sphere — closed in u AND v) is bound
    # only by a degenerate VERTEX_LOOP, which the reader filters out, leaving no edge
    # bounds. Build the natural full face straight from the surface; OCC generates the
    # seam(s) and poles itself (the only robust way — there is no wire to reconstruct).
    if not advanced_face.bounds:
        mk = BRepBuilderAPI_MakeFace(face_surface, 1e-6)
        if mk.IsDone():
            return mk.Face()
        raise ValueError("AdvancedFace has no bounds and the surface has no natural face")

    is_bspline_surface = isinstance(face_surface, Geom_BSplineSurface)

    def _build_bspline_wire(face_bound, builder) -> tuple:
        """Build an OCC wire for a BSpline-surface face by walking
        ``face_bound.bound.edge_list`` directly (rather than via
        ``make_wire_from_face_bound`` + post-hoc TopExp_Explorer match).

        Constructing the OCC edges in the same order as the source
        ``OrientedEdge`` list lets us pass ``supplied_pcurves`` 1:1 to
        ``update_edges_uv_gen`` without endpoint-matching guesswork.
        Pcurves are attached BEFORE adding the edge to the wire builder,
        so any reordering ``BRepBuilderAPI_MakeWire`` does internally
        cannot decouple them — pcurves travel with the OCC edge handle.

        Returns (wire, n_updated, n_total).
        """
        # SAT-supplied UV pcurves are now consumed by default. The fix
        # that made this work: when a coedge has a pcurve, build the
        # OCC edge via ``BRepBuilderAPI_MakeEdge(c2d, surface, t1, t2)``
        # so OCCT derives the 3D parametrization from
        # ``surface(pcurve(t))`` — guaranteed-consistent with the 2D
        # curve. SAT also stores a separate 3D BSpline curve per edge,
        # but its parameterization is independent of the pcurve's, and
        # for many OP1 faces the two take different *paths* between the
        # same endpoints. Using both together produced visually-stretched
        # faces; using only the pcurve+surface (the path STEP imports
        # take naturally) produces ~96% byte-for-byte agreement with the
        # regen baseline at ~5x the speed.
        #
        # Override knobs (default both ON):
        #   ADA_USE_SAT_PCURVES=false    skip SAT pcurves entirely → regen
        #   ADA_PCURVE_DRIVE_EDGE=false  attach pcurve via UpdateEdge
        #                                instead of building edge from it
        #                                (the older, broken approach;
        #                                left for diagnostics)
        import os as _os

        def _env_truthy(name: str, default: bool) -> bool:
            v = (_os.environ.get(name) or "").strip().lower()
            if v in {"1", "true", "yes", "on"}:
                return True
            if v in {"0", "false", "no", "off"}:
                return False
            return default

        use_pcurves = _env_truthy("ADA_USE_SAT_PCURVES", True)
        drive_edge_from_pcurve = _env_truthy("ADA_PCURVE_DRIVE_EDGE", True)
        edge_list = getattr(face_bound.bound, "edge_list", None) or []
        occ_edges: list = []
        pcurves: list = []
        for oe in edge_list:
            supplied_pc = getattr(oe, "pcurve", None) if use_pcurves else None
            occ_edge = None
            if drive_edge_from_pcurve and supplied_pc is not None:
                occ_edge = _make_edge_from_pcurve(supplied_pc, face_surface)
                if occ_edge is not None:
                    # Edge already carries a consistent 2D pcurve from
                    # the constructor; no UpdateEdge needed.
                    occ_edges.append(occ_edge)
                    pcurves.append(None)
                    continue
            try:
                occ_edge = make_edge_from_edge(oe)
            except (RuntimeError, UnableToCreateCurveOCCGeom) as ex:
                logger.debug("dropped degenerate edge in BSpline-surface bound: %s", ex)
                continue
            occ_edges.append(occ_edge)
            pcurves.append(supplied_pc)
        if not occ_edges:
            raise UnableToCreateTesselationFromSolidOCCGeom("BSpline-surface bound produced no usable edges")
        n_updated, n_total = update_edges_uv_gen(
            occ_edges,
            builder,
            face_surface,
            supplied_pcurves=pcurves,
        )
        # First attempt: default OCC vertex-snap tolerance.
        wire_maker = BRepBuilderAPI_MakeWire()
        for e in occ_edges:
            wire_maker.Add(e)
        wire_maker.Build()
        if not wire_maker.IsDone():
            # Relaxed pass. The default tolerance is a few microns,
            # which fails when SAT-derived edge endpoints land on
            # subtly-different vertex hash bins (drive_edge picks
            # endpoints from the surface's pcurve evaluation;
            # make_edge_from_edge picks them from the 3D-curve
            # endpoint snap — the two can differ by 0.1-1 mm). 1 mm
            # is well below structural feature size on the SAT plate
            # geometries we see and matches OCC's own tolerance bumps in
            # ShapeFix_Wire when it sees gaps below that.
            from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeWire as _MW

            wire_maker = _MW()
            try:
                wire_maker.SetTolerance(1.0e-3)
            except Exception:
                pass
            for e in occ_edges:
                wire_maker.Add(e)
            wire_maker.Build()
        if not wire_maker.IsDone():
            raise UnableToCreateTesselationFromSolidOCCGeom("BRepBuilderAPI_MakeWire failed for BSpline-surface bound")
        return wire_maker.Wire(), n_updated, n_total

    if is_bspline_surface:
        builder = BRep_Builder()
        outer_wire, n_updated, n_total = _build_bspline_wire(advanced_face.bounds[0], builder)
        if n_updated < n_total:
            # Any failed p-curve update on a BSpline-surface wire is a
            # crash trigger downstream — bail rather than feed the
            # half-attached wire into MakeFace/ShapeFix.
            raise UnableToCreateTesselationFromSolidOCCGeom(
                f"p-curve update incomplete ({n_updated}/{n_total}); skipping degenerate BSpline face."
            )

        if is_wire_cw(outer_wire, face_surface):
            logger.info("Reversing CW wire to CCW for B-Spline surface")
            outer_wire = outer_wire.Reversed()
    else:
        # A closed cylinder/cone face (full circle in u) can't be built from a wire
        # of full-circle edges + a doubled seam — build it from the surface's
        # parametric bounds instead, letting OCC generate the seam.
        closed_face = _try_make_closed_revolution_face(advanced_face, face_surface)
        if closed_face is not None:
            return closed_face
        outer_wire = make_wire_from_face_bound(advanced_face.bounds[0])

    face_maker = BRepBuilderAPI_MakeFace(face_surface, outer_wire)

    # Add inner wires (holes) if present
    if len(advanced_face.bounds) > 1:
        for inner_fb in advanced_face.bounds[1:]:
            try:
                if is_bspline_surface:
                    inner_wire, n_updated, n_total = _build_bspline_wire(inner_fb, builder)
                    if n_updated < n_total:
                        raise UnableToCreateTesselationFromSolidOCCGeom(
                            f"inner bound p-curve update incomplete ({n_updated}/{n_total})"
                        )
                else:
                    inner_wire = make_wire_from_face_bound(inner_fb)
                face_maker.Add(inner_wire)
            except UnableToCreateTesselationFromSolidOCCGeom:
                # Bubble the skip up — partial inner bounds on a degenerate
                # BSpline face would crash the same as the outer case.
                raise
            except Exception as ex:
                logger.warning(f"Skipping inner bound due to error creating wire: {ex}")

    if not face_maker.IsDone():
        raise Exception(f"Failed to create face from surface type {type(advanced_face.face_surface)}")

    face = face_maker.Face()

    # ShapeFix runs only on the regenerative-pcurve path — the
    # SAT-pcurve path produces clean topology and ShapeFix would
    # otherwise rebuild our authored p-curves to match its own
    # conventions, undoing the consistency we just established.
    # ADA_SKIP_SHAPEFIX=true forces a skip even on the regen path.
    import os as _os_sf

    skip_shapefix = (_os_sf.environ.get("ADA_SKIP_SHAPEFIX") or "").strip().lower() in {"1", "true", "yes", "on"}
    use_pcurves_env_sf = (_os_sf.environ.get("ADA_USE_SAT_PCURVES") or "").strip().lower()
    # Default: use_pcurves is ON. ShapeFix should fire only when
    # pcurves are explicitly OFF (regen path) AND the user hasn't set
    # ADA_SKIP_SHAPEFIX.
    use_pcurves_sf = use_pcurves_env_sf not in {"0", "false", "no", "off"}
    if isinstance(face_surface, Geom_BSplineSurface) and not (skip_shapefix or use_pcurves_sf):
        fixer = ShapeFix_Face(face)
        fixer.Perform()
        # Explicitly run wire fixes
        wire_fixer = fixer.FixWireTool()
        wire_fixer.FixConnected()
        wire_fixer.FixClosed()

        face = fixer.Face()

    # Update the face tolerance
    builder = BRep_Builder()
    builder.UpdateFace(face, 1e-3)

    return face


def make_plane_from_geom(plane: geo_su.Plane) -> gp_Pln:
    location = plane.position.location
    axis = plane.position.axis
    ref_direction = plane.position.ref_direction

    # Define the origin point of the plane
    origin = gp_Pnt(*location)

    # Define the normal to the plane
    normal = gp_Dir(*axis)

    # Define the reference direction to orient the plane
    ref_dir = gp_Dir(*ref_direction)

    # Create an Ax3 object using the origin, normal, and reference direction
    # The gp_Ax3 constructor with an origin, normal direction, and X-direction
    ax3 = gp_Ax3(origin, normal, ref_dir)

    # Create the plane using gp_Pln from the Ax3 object
    return gp_Pln(ax3)


def make_cylindrical_surface_from_geom(cylinder: geo_su.CylindricalSurface) -> Geom_CylindricalSurface:
    location = cylinder.position.location
    axis = cylinder.position.axis
    ref_direction = cylinder.position.ref_direction

    # Define the origin point of the cylinder
    origin = gp_Pnt(*location)

    # Define the axis direction
    axis_dir = gp_Dir(*axis)

    # Define the reference direction
    ref_dir = gp_Dir(*ref_direction)

    # Create an Ax3 object
    ax3 = gp_Ax3(origin, axis_dir, ref_dir)

    # Create the cylindrical surface
    return Geom_CylindricalSurface(gp_Cylinder(ax3, cylinder.radius))


def make_conical_surface_from_geom(cone: geo_su.ConicalSurface) -> Geom_ConicalSurface:
    location = cone.position.location
    axis = cone.position.axis
    ref_direction = cone.position.ref_direction

    # Define the origin point of the cone
    origin = gp_Pnt(*location)

    # Define the axis direction
    axis_dir = gp_Dir(*axis)

    # Define the reference direction
    ref_dir = gp_Dir(*ref_direction)

    # Create an Ax3 object
    ax3 = gp_Ax3(origin, axis_dir, ref_dir)

    # Create the conical surface
    return Geom_ConicalSurface(gp_Cone(ax3, cone.semi_angle, cone.radius))


def make_spherical_surface_from_geom(sphere: geo_su.SphericalSurface) -> Geom_SphericalSurface:
    location = sphere.position.location
    axis = sphere.position.axis
    ref_direction = sphere.position.ref_direction

    # Define the origin point of the sphere
    origin = gp_Pnt(*location)

    # Define the axis direction
    axis_dir = gp_Dir(*axis)

    # Define the reference direction
    ref_dir = gp_Dir(*ref_direction)

    # Create an Ax3 object
    ax3 = gp_Ax3(origin, axis_dir, ref_dir)

    # Create the spherical surface
    return Geom_SphericalSurface(gp_Sphere(ax3, sphere.radius))


def make_toroidal_surface_from_geom(torus: geo_su.ToroidalSurface) -> Geom_ToroidalSurface:
    location = torus.position.location
    axis = torus.position.axis
    ref_direction = torus.position.ref_direction

    # Define the origin point of the torus
    origin = gp_Pnt(*location)

    # Define the axis direction
    axis_dir = gp_Dir(*axis)

    # Define the reference direction
    ref_dir = gp_Dir(*ref_direction)

    # Create an Ax3 object
    ax3 = gp_Ax3(origin, axis_dir, ref_dir)

    # Create the toroidal surface
    return Geom_ToroidalSurface(gp_Torus(ax3, torus.major_radius, torus.minor_radius))


def make_surface_from_geom(face_surface):
    """
    Create an OCC surface from an adapy surface geometry definition.

    Args:
        face_surface: Any supported adapy surface type

    Returns:
        OCC surface object (gp_Pln, Geom_CylindricalSurface, etc.)
    """
    if type(face_surface) is geo_su.Plane:
        return make_plane_from_geom(face_surface)
    elif type(face_surface) is geo_su.CylindricalSurface:
        return make_cylindrical_surface_from_geom(face_surface)
    elif type(face_surface) is geo_su.ConicalSurface:
        return make_conical_surface_from_geom(face_surface)
    elif type(face_surface) is geo_su.SphericalSurface:
        return make_spherical_surface_from_geom(face_surface)
    elif type(face_surface) is geo_su.ToroidalSurface:
        return make_toroidal_surface_from_geom(face_surface)
    elif type(face_surface) in (geo_su.BSplineSurfaceWithKnots, geo_su.RationalBSplineSurfaceWithKnots):
        return make_bspline_surface_with_knots(face_surface)
    else:
        raise NotImplementedError(f"Surface type {type(face_surface)} is not implemented")


def _face_area(shape) -> float:
    """Surface area of an OCC shape (0.0 if it can't be measured). Used to detect a
    trimmed face that collapsed to nothing."""
    from OCC.Core.BRepGProp import brepgprop
    from OCC.Core.GProp import GProp_GProps

    try:
        props = GProp_GProps()
        brepgprop.SurfaceProperties(shape, props)
        return abs(props.Mass())
    except Exception:
        return 0.0


def _add_cfs_faces_to_shell(builder: BRep_Builder, occ_shell: TopoDS_Shell, cfs_faces) -> None:
    """Build each connected-face-set face (AdvancedFace / FaceSurface) and add it to
    ``occ_shell``. Shared by the closed-shell, open-shell and shell-based-surface-model
    builders — a face that can't be built is logged and skipped rather than aborting."""
    n_faces = 0
    n_dropped = 0
    for cfs_face in cfs_faces:
        # Handle AdvancedFace
        if type(cfs_face) is geo_su.AdvancedFace:
            n_faces += 1
            try:
                # Route through make_face_from_geom so closed (seam) cylinder/cone/torus
                # faces, B-spline faces and inner-bound holes build here exactly as on
                # the single-face path — not just the simple wire + MakeFace this builder
                # used to do, which dropped those faces (holes in the solid).
                face = make_face_from_geom(cfs_face)
                if face is None or face.IsNull():
                    raise UnableToCreateTesselationFromSolidOCCGeom("make_face_from_geom produced no face")

                # A trimmed surface can collapse to zero area even when MakeFace reports
                # "done" — e.g. a planar SAT plate whose boundary mixes a b-spline edge that
                # yields no valid p-curve on the plane, so the face has no interior and
                # BRepMesh grids nothing. Fall back to filling the boundary wire directly
                # (the WireFilledFace path), which reconstructs a real surface from the same
                # closed wire.
                if _face_area(face) <= 1e-9:
                    try:
                        filled = make_face_from_wire_filled(geo_su.WireFilledFace(bounds=cfs_face.bounds))
                        if _face_area(filled) > 1e-9:
                            face = filled
                    except Exception as ex:
                        logger.debug("AdvancedFace wire-fill fallback failed: %s", ex)

                # Update the face tolerance
                builder.UpdateFace(face, 1e-6)

                # Add the face to the shell
                builder.Add(occ_shell, face)
            except Exception as ex:
                n_dropped += 1
                logger.warning("Skipping AdvancedFace (%s surface): %s", type(cfs_face.face_surface).__name__, ex)
                continue

        # Handle FaceSurface (legacy support)
        elif type(cfs_face) is geo_su.FaceSurface:
            n_faces += 1
            face_surface = cfs_face.face_surface
            if type(face_surface) is geo_su.Plane:
                occ_face_surface = make_plane_from_geom(face_surface)
            else:
                raise NotImplementedError(f"Only Plane is implemented for FaceSurface, not {type(face_surface)}")

            try:
                wire = make_wire_from_edge_loop(cfs_face.bounds[0].bound)

                face_maker = BRepBuilderAPI_MakeFace(occ_face_surface, wire)
                if not face_maker.IsDone():
                    logger.warning("Failed to create face from surface; skipping face")
                    continue

                face = face_maker.Face()

                # Update the face tolerance
                builder.UpdateFace(face, 1e-6)

                # Add the face to the shell
                builder.Add(occ_shell, face)
            except Exception as ex:
                n_dropped += 1
                logger.warning(f"Skipping FaceSurface due to error during wire/face creation: {ex}")
                continue
        else:
            raise NotImplementedError(
                f"Face type {type(cfs_face)} is not implemented (supported: AdvancedFace, FaceSurface)"
            )

    if n_dropped:
        # A dropped face is a hole in the solid — surface it so conversions never
        # silently lose geometry.
        logger.warning("shell build dropped %d/%d faces (holes in the solid)", n_dropped, n_faces)


def make_closed_shell_from_geom(shell: geo_su.ClosedShell) -> TopoDS_Shell:
    builder = BRep_Builder()
    occ_shell = TopoDS_Shell()
    builder.MakeShell(occ_shell)
    _add_cfs_faces_to_shell(builder, occ_shell, shell.cfs_faces)
    occ_shell.Closed(True)
    return occ_shell


def make_open_shell_from_geom(shell: geo_su.OpenShell) -> TopoDS_Shell:
    """Open (non-watertight) shell — a surface patch set, not a closed solid. Same face
    construction as the closed shell, but left un-flagged so downstream code renders/exports
    it as a surface rather than attempting solid operations on it."""
    builder = BRep_Builder()
    occ_shell = TopoDS_Shell()
    builder.MakeShell(occ_shell)
    _add_cfs_faces_to_shell(builder, occ_shell, shell.cfs_faces)
    return occ_shell


def make_shell_from_shell_based_surface_geom(sbsm: geo_su.ShellBasedSurfaceModel) -> TopoDS_Shape:
    """Build an IfcShellBasedSurfaceModel — a set of open/closed shells — into a single OCC
    compound of shells, so a multi-shell wall/cladding surface renders and exports."""
    builder = BRep_Builder()
    compound = TopoDS_Compound()
    builder.MakeCompound(compound)
    for boundary in sbsm.sbsm_boundary:
        occ_shell = (
            make_closed_shell_from_geom(boundary)
            if isinstance(boundary, geo_su.ClosedShell)
            else (make_open_shell_from_geom(boundary))
        )
        builder.Add(compound, occ_shell)
    return compound


def make_face_from_curve(outer_curve: geo_cu.CURVE_GEOM_TYPES):
    if isinstance(outer_curve, geo_cu.IndexedPolyCurve):
        return make_face_from_indexed_poly_curve_geom(outer_curve)
    elif isinstance(outer_curve, geo_cu.Circle):
        return make_face_from_circle(outer_curve)
    else:
        raise NotImplementedError("Only IndexedPolyCurve is implemented")


def make_profile_from_geom(area: geo_su.ProfileDef) -> TopoDS_Shape | TopoDS_Face:
    if isinstance(area, geo_su.ProfileDef) and not isinstance(area, geo_su.ArbitraryProfileDef):
        # Parametric profile (I/T/...) -> derive a buildable arbitrary outline (shared with
        # the adacpp backend; keeps the geom-level representation parametric).
        from ada.api.beams.geom_beams import parametric_profile_to_arbitrary

        area = parametric_profile_to_arbitrary(area)

    if not isinstance(area, geo_su.ArbitraryProfileDef):
        raise NotImplementedError("Only ArbitraryProfileDefWithVoids is implemented")

    if area.profile_type == geo_su.ProfileType.AREA:
        profile = make_face_from_curve(area.outer_curve)

        for inner in area.inner_curves:
            try:
                inner_face = make_face_from_curve(inner)
            except ValueError as e:
                # Typical case for “pipe with t ~= r”: inner radius ~ 0 or tiny negative
                logger.warning(f"[profile] Skipping inner void curve (treated as solid): {e}")
                logger.warning(f"[profile] Inner curve: {inner!r}")
                continue

            profile = BRepAlgoAPI_Cut(profile, inner_face).Shape()

        return profile

    # non-area profile
    profile = make_wire_from_curve(area.outer_curve)
    for inner in area.inner_curves:
        try:
            inner_wire = make_wire_from_curve(inner)
        except ValueError as e:
            logger.warning(f"[profile] Skipping inner wire: {e}")
            logger.warning(f"[profile] Inner curve: {inner!r}")
            continue
        profile = BRepAlgoAPI_Cut(profile, inner_wire).Shape()

    return profile


def make_face_from_wire_filled(wff: geo_su.WireFilledFace) -> TopoDS_Face:
    """Build an OCC face from a wire-only ``WireFilledFace``.

    Uses ``BRepFill_Filling`` (via ``BRepOffsetAPI_MakeFilling``) to
    interpolate a smooth surface through the boundary edges, then trims
    the surface to the boundary wire. Without explicit trimming, the
    surface generated by MakeFilling can extend several metres past the
    wire — even though the *face* should be clipped to the wire, the
    underlying surface's parameter domain isn't bounded, so downstream
    consumers that sample the face (BRepMesh, prism extrusion) can hit
    the unbounded region.
    """
    from OCC.Core.BRepBuilderAPI import BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire
    from OCC.Core.BRepOffsetAPI import BRepOffsetAPI_MakeFilling
    from OCC.Core.GeomAbs import GeomAbs_C0
    from OCC.Core.ShapeFix import ShapeFix_Face

    if not wff.bounds:
        raise UnableToCreateTesselationFromSolidOCCGeom("WireFilledFace has no bounds")

    primary = wff.bounds[0]
    edge_list = getattr(primary.bound, "edge_list", None) or []
    if len(edge_list) < 3:
        raise UnableToCreateTesselationFromSolidOCCGeom(f"WireFilledFace needs ≥3 boundary edges, got {len(edge_list)}")

    occ_edges: list = []
    for oe in edge_list:
        try:
            occ_edges.append(make_edge_from_edge(oe))
        except Exception as ex:
            logger.debug("WireFilledFace: dropped degenerate edge: %s", ex)
    if len(occ_edges) < 3:
        raise UnableToCreateTesselationFromSolidOCCGeom("WireFilledFace: <3 usable edges after conversion")

    # Step 1: stitch edges into a single closed wire. SAT-derived edge
    # endpoints can land on subtly-different vertex hashes (sub-mm
    # gaps), so retry with a relaxed tolerance — same trick the
    # AdvancedFace builder uses.
    wire_maker = BRepBuilderAPI_MakeWire()
    for e in occ_edges:
        wire_maker.Add(e)
    wire_maker.Build()
    if not wire_maker.IsDone():
        wire_maker = BRepBuilderAPI_MakeWire()
        try:
            wire_maker.SetTolerance(1.0e-3)
        except Exception:
            pass
        for e in occ_edges:
            wire_maker.Add(e)
        wire_maker.Build()
        if not wire_maker.IsDone():
            raise UnableToCreateTesselationFromSolidOCCGeom("WireFilledFace: failed to assemble wire from edges")
    wire = wire_maker.Wire()

    # Step 2: build a fitted surface from the boundary edges. Adding
    # edges individually with GeomAbs_C0 lets MakeFilling solve the
    # least-squares system that minimises bending under the boundary
    # constraints.
    fill = BRepOffsetAPI_MakeFilling()
    for e in occ_edges:
        fill.Add(e, GeomAbs_C0)
    try:
        fill.Build()
    except Exception as ex:
        raise UnableToCreateTesselationFromSolidOCCGeom(f"BRepOffsetAPI_MakeFilling failed: {ex}")
    if not fill.IsDone():
        raise UnableToCreateTesselationFromSolidOCCGeom("BRepOffsetAPI_MakeFilling did not complete")
    fill_shape = fill.Shape()

    # The shape returned by MakeFilling is already a TopoDS_Face, but
    # its outer wire is what MakeFilling derived from the constraint
    # edges — which can wander beyond the input wire when the constraint
    # solver projects edges onto the fitted surface. Re-trim by
    # extracting the underlying Geom_Surface and rebuilding the face
    # with our wire as the explicit boundary.
    exp = TopExp_Explorer(fill_shape, TopAbs_FACE)
    if not exp.More():
        raise UnableToCreateTesselationFromSolidOCCGeom("MakeFilling produced no face")
    fitted_face = exp.Current()
    fitted_surf = BRep_Tool.Surface(fitted_face)

    try:
        face_maker = BRepBuilderAPI_MakeFace(fitted_surf, wire, True)
        face_maker.Build()
        if face_maker.IsDone():
            rebuilt = face_maker.Face()
            # Apply ShapeFix to clean up any 2D pcurve issues that
            # the MakeFace-with-wire path introduces (rare, but
            # cheap insurance against malformed faces).
            fixer = ShapeFix_Face(rebuilt)
            fixer.Perform()
            return fixer.Face()
    except Exception as ex:
        logger.debug("WireFilledFace: re-trim failed (%s); using fitted face", ex)

    # Re-trim failed — return the original fitted face. Worst case,
    # the bbox is larger than the wire, but the face is still a valid
    # G0-continuous interpolation through the boundary.
    return fitted_face
