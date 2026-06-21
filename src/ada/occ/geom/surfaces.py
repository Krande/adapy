import math
from collections import Counter

from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRep import BRep_Builder, BRep_Tool
from OCC.Core.BRepAlgoAPI import BRepAlgoAPI_Cut, BRepAlgoAPI_Fuse
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge,
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_Sewing,
)
from OCC.Core.BRepTools import BRepTools_WireExplorer, breptools
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


def _pcurve_trim_range(c2d, face_surface, edge_start, edge_end):
    """Sub-range ``[s_a, s_b]`` of the pcurve whose 3D image spans the edge.

    A single SAT pcurve is often *shared* by several coedges along one UV side
    of a face — each coedge is a different sub-segment, but the explicit pcurve
    carries the whole side's UV trajectory. The edge's own ``[t_start, t_end]``
    is in the 3D-curve's parameter space, which is offset/reversed relative to
    the pcurve's, so we can't slice the pcurve by it directly. Instead, map the
    edge's *3D endpoints* into pcurve space: sample the pcurve, push each sample
    through the surface, and pick the parameters whose 3D images are nearest the
    edge's two endpoints. Returns ``None`` when the pcurve already matches the
    edge's length (no trim needed) or the endpoints aren't usable."""
    if edge_start is None or edge_end is None:
        return None
    s0 = float(c2d.FirstParameter())
    s1 = float(c2d.LastParameter())
    if abs(s1 - s0) <= 1e-9:
        return None
    import numpy as _np

    a = _np.asarray(list(edge_start)[:3], dtype=float)
    b = _np.asarray(list(edge_end)[:3], dtype=float)
    edge_len = float(_np.linalg.norm(b - a))
    if edge_len <= 1e-9:
        return None
    tol = 0.05 * edge_len  # endpoints "coincide" within 5% of the edge length

    def _surf3d(s):
        uv = c2d.Value(s)
        p = face_surface.Value(uv.X(), uv.Y())
        return _np.array([p.X(), p.Y(), p.Z()])

    # Cheap common-case test: when the pcurve's own ends already land on the
    # edge's endpoints (either order), the edge uses the whole pcurve — no trim,
    # so skip the 200-sample scan. Only a *shared* pcurve (longer than the edge)
    # extends past the endpoints and needs trimming.
    e0, e1 = _surf3d(s0), _surf3d(s1)
    ends_match = (_np.linalg.norm(e0 - a) <= tol and _np.linalg.norm(e1 - b) <= tol) or (
        _np.linalg.norm(e0 - b) <= tol and _np.linalg.norm(e1 - a) <= tol
    )
    if ends_match:
        return None

    ss = _np.linspace(s0, s1, 200)
    pts = _np.array([_surf3d(s) for s in ss])
    s_a = float(ss[int(_np.argmin(_np.linalg.norm(pts - a, axis=1)))])
    s_b = float(ss[int(_np.argmin(_np.linalg.norm(pts - b, axis=1)))])
    lo, hi = (s_a, s_b) if s_a <= s_b else (s_b, s_a)
    if hi - lo <= 1e-9:
        return None
    return lo, hi


def _make_edge_from_pcurve(pcurve_geom, face_surface, edge_start=None, edge_end=None):
    """Build an OCC edge from a 2D BSpline pcurve + the face's surface.

    The 3D parametrization is derived implicitly by OCCT from
    surface(pcurve(t)), so 2D and 3D are guaranteed-consistent.

    ``edge_start`` / ``edge_end`` (the coedge's 3D endpoints) let us trim a
    *shared* pcurve to this coedge's sub-segment — without the trim, a
    split-edge wire traces the whole UV side twice, self-intersects, and
    BRepMesh grids only half the face (the hull-skin "missing triangles").
    Returns None on any failure so the caller falls back to building from the
    SAT-supplied 3D BSpline + reparam.
    """
    c2d = _build_geom2d_bspline(pcurve_geom)
    if c2d is None:
        return None
    try:
        first = float(c2d.FirstParameter())
        last = float(c2d.LastParameter())
        try:
            trim = _pcurve_trim_range(c2d, face_surface, edge_start, edge_end)
        except Exception as ex:
            logger.debug("pcurve trim probe failed, using full range: %s", ex)
            trim = None
        if trim is not None:
            first, last = trim
        maker = BRepBuilderAPI_MakeEdge(c2d, face_surface, first, last)
        if not maker.IsDone():
            return None
        return maker.Edge()
    except Exception as ex:
        logger.warning(f"BRepBuilderAPI_MakeEdge(c2d, surface) failed: {ex}")
        return None


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
        # A supplied pcurve reaching here means the drive-edge path could not
        # build this coedge from it, so reproject the 3D curve onto the surface
        # (regen path below). The old "attach the raw UV curve via affine knot
        # remap" fast path was removed: it mis-stretched shared pcurves onto each
        # coedge's range and produced self-intersecting wires / zero-area faces.
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
    """World-space sample points along one oriented edge. A circular edge is sampled
    along its arc (the full period for a closed circle) so the projected parameter
    range captures the swept direction; other edges contribute their endpoints."""
    import math

    pts = [
        (float(oe.start[0]), float(oe.start[1]), float(oe.start[2])),
        (float(oe.end[0]), float(oe.end[1]), float(oe.end[2])),
    ]
    ec = getattr(oe, "edge_element", oe)
    g = getattr(ec, "edge_geometry", None)
    if isinstance(g, geo_cu.Circle):
        pos = g.position
        c = [float(x) for x in pos.location]
        z = [float(x) for x in pos.axis]
        xd = [float(x) for x in pos.ref_direction]
        yd = (z[1] * xd[2] - z[2] * xd[1], z[2] * xd[0] - z[0] * xd[2], z[0] * xd[1] - z[1] * xd[0])
        r = float(g.radius)
        if _points_close(oe.start, oe.end):
            a0, a1 = 0.0, 2.0 * math.pi
        else:
            # Partial arc: sample between the endpoint angles ALONG THE OCCUPIED ARC.
            # The arc's point-set is fully determined by the EdgeCurve alone: from
            # start to end going in the circle's positive parametric direction when
            # ``same_sense``, negative otherwise. Crucially this ignores
            # ``OrientedEdge.orientation`` — readers differ on whether they pre-swap
            # the oriented edge's endpoints (the STEP stream reader does, the SAT
            # converter doesn't), but every producer authors EdgeCurve(start, end,
            # same_sense) with the same semantics. Sampling the complement arc here
            # widened the parameter extent to the full period, so a small wedge face
            # rebuilt as a spurious full cylinder/cone band.
            def _ang(p):
                d = (p[0] - c[0], p[1] - c[1], p[2] - c[2])
                return math.atan2(
                    d[0] * yd[0] + d[1] * yd[1] + d[2] * yd[2],
                    d[0] * xd[0] + d[1] * xd[1] + d[2] * xd[2],
                )

            same_sense = bool(getattr(ec, "same_sense", True))
            ec_start = getattr(ec, "start", oe.start)
            ec_end = getattr(ec, "end", oe.end)
            a0, a1 = _ang(ec_start), _ang(ec_end)
            if same_sense:
                if a1 <= a0:
                    a1 += 2.0 * math.pi
            else:
                if a1 >= a0:
                    a1 -= 2.0 * math.pi
        for k in range(13):
            a = a0 + (a1 - a0) * k / 12.0
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


# Fidelity accounting for the param-extent repair paths. Consumed (return-and-reset)
# by the streaming conversion so its summary can quantify how much geometry was
# rebuilt/approximated/dropped — see consume_param_rebuild_stats().
PARAM_REBUILD_STATS: Counter = Counter()


def consume_param_rebuild_stats() -> dict[str, int]:
    """Return and reset the per-process rebuild counters (picked up after each solid
    by the streaming tessellation worker)."""
    out = dict(PARAM_REBUILD_STATS)
    PARAM_REBUILD_STATS.clear()
    return out


# Per-face build coverage: how many connected-face-set faces were attempted vs how
# many actually built into the OCC shell (the rest are holes — dropped faces). The
# mesh-stage coverage (built-but-unmeshed) is measured separately by
# ``ada.cadit.diagnostics.face_coverage``; this counter feeds the streaming summary
# cheaply without a second build. Keys: "total", "built", "dropped".
FACE_COVERAGE_STATS: Counter = Counter()


def consume_face_coverage_stats() -> dict[str, int]:
    """Return and reset the per-process face-build coverage counters."""
    out = dict(FACE_COVERAGE_STATS)
    FACE_COVERAGE_STATS.clear()
    return out


# A rebuilt face whose area exceeds this multiple of (boundary-sample bbox diagonal)^2
# is over-covering its own boundary evidence and gets dropped instead. Legitimate
# closed revolution faces stay far below: a full cylinder's worst area/diag^2 ratio is
# ~1.11 (at h = 2*sqrt(2)*r), a torus tube ~1.23 — 4.0 leaves >3x margin, while the
# failure mode (full-period band rebuilt from a small wedge's evidence) overshoots by
# ~period/arc_span.
_REBUILD_AREA_GATE = 4.0


def _is_closure_bound(fb, face_surface) -> bool:
    """True when every edge of the bound is a full circle concentric with the
    revolution surface's axis — the bound that *defines* a closed face's extent
    (e.g. a full cylinder's top/bottom rims, which often arrive as two separate
    bounds). Such a bound is no hole and must never be re-added as an inner wire."""
    pos = getattr(face_surface, "Position", None)
    if pos is None:
        return False
    ax = pos().Axis()
    a_loc, a_dir = ax.Location(), ax.Direction()
    edges = getattr(getattr(fb, "bound", None), "edge_list", None) or []
    if not edges:
        return False
    for oe in edges:
        ec = getattr(oe, "edge_element", oe)
        g = getattr(ec, "edge_geometry", None)
        if not isinstance(g, geo_cu.Circle) or not _points_close(oe.start, oe.end):
            return False
        c = [float(x) for x in g.position.location]
        z = [float(x) for x in g.position.axis]
        r = float(g.radius)
        # circle axis parallel to the surface axis
        dot = abs(z[0] * a_dir.X() + z[1] * a_dir.Y() + z[2] * a_dir.Z())
        zlen = math.sqrt(z[0] ** 2 + z[1] ** 2 + z[2] ** 2) or 1.0
        if dot / zlen < 1.0 - 1e-6:
            return False
        # circle centre on the surface axis (within tol*r)
        dx = c[0] - a_loc.X()
        dy = c[1] - a_loc.Y()
        dz = c[2] - a_loc.Z()
        along = dx * a_dir.X() + dy * a_dir.Y() + dz * a_dir.Z()
        off2 = dx * dx + dy * dy + dz * dz - along * along
        if off2 > (1e-4 * max(r, 1.0)) ** 2:
            return False
    return True


def _make_face_from_param_extent(advanced_face: geo_su.AdvancedFace, face_surface):
    """Build a face directly from the projected parameter extent of its boundary
    samples — for faces whose boundary wire cannot trim the surface (closed
    revolution faces, seam-crossing arc wires). Hole bounds are re-added when their
    wires build; a rebuild whose area overruns its own boundary evidence is rejected
    (None) — a dropped face beats a spurious full cone. Returns a face or None."""
    # Recover (u, v) parameter ranges by projecting the boundary samples onto the
    # surface; a closed direction snaps to the full period via _param_extent.
    us: list[float] = []
    vs: list[float] = []
    sample_pts: list[tuple] = []
    for fb in advanced_face.bounds:
        for oe in getattr(getattr(fb, "bound", None), "edge_list", []):
            for p in _sample_edge_points(oe):
                proj = GeomAPI_ProjectPointOnSurf(gp_Pnt(*p), face_surface)
                if proj.NbPoints() > 0:
                    u, v = proj.LowerDistanceParameters()
                    us.append(u)
                    vs.append(v)
                    sample_pts.append(p)
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
    face = mk.Face()

    # Area sanity gate: the boundary samples are world points ON the face's rim, so
    # the face cannot legitimately dwarf their bounding box. Catching it here (rather
    # than emitting) is what turns "spurious giant cone obfuscating the model" into a
    # small hole.
    lo = [min(p[i] for p in sample_pts) for i in range(3)]
    hi = [max(p[i] for p in sample_pts) for i in range(3)]
    diag2 = (hi[0] - lo[0]) ** 2 + (hi[1] - lo[1]) ** 2 + (hi[2] - lo[2]) ** 2
    area = _face_area(face)
    if area > _REBUILD_AREA_GATE * max(diag2, 1e-12):
        PARAM_REBUILD_STATS["area_gate_dropped"] += 1
        logger.debug(
            "param-extent rebuild over-covers boundary evidence (area %.3g > %.0fx diag^2 %.3g); dropping face",
            area,
            _REBUILD_AREA_GATE,
            diag2,
        )
        return None

    # Re-add hole bounds. ``bounds[0]`` is the outer boundary (the same assumption
    # the normal wire path makes) and is already represented by the parameter
    # extent — punching it as a hole would consume the face. Of the rest, a closure
    # bound (full concentric circles — e.g. a closed cylinder's second rim) defines
    # the extent and is NOT a hole; anything else is a cutout the plain param-range
    # face would otherwise fill. Each hole must shrink the area — a wire that
    # doesn't (wrong orientation / off-surface) is skipped rather than risked.
    holes = [fb for fb in advanced_face.bounds[1:] if not _is_closure_bound(fb, face_surface)]
    for fb in holes:
        try:
            wire = make_wire_from_face_bound(fb)
        except Exception as ex:  # noqa: BLE001 - holes are best-effort fidelity
            logger.debug("param-extent rebuild: inner bound wire failed (%s)", ex)
            PARAM_REBUILD_STATS["inner_bound_dropped"] += 1
            continue
        # A hole wire must wind opposite to the outer boundary; source files (and
        # make_wire_from_face_bound) don't guarantee which sense arrives, so try
        # both and keep whichever SHRINKS the area without zeroing it. The added
        # wire has no pcurves on this parametric face, so ShapeFix_Face computes
        # them (safe here: the face is BOUNDED — the known ShapeFix segfaults are
        # on faces still carrying infinite natural bounds).
        added = False
        for candidate in (wire, wire.Reversed()):
            try:
                mk2 = BRepBuilderAPI_MakeFace(face)
                mk2.Add(candidate)
                if not mk2.IsDone():
                    continue
                fixer = ShapeFix_Face(mk2.Face())
                fixer.Perform()
                fixed = fixer.Face()
                if 1e-12 < _face_area(fixed) < area:
                    face = fixed
                    area = _face_area(face)
                    PARAM_REBUILD_STATS["inner_bound_readded"] += 1
                    added = True
                    break
            except Exception as ex:  # noqa: BLE001
                logger.debug("param-extent rebuild: inner bound add failed (%s)", ex)
        if not added:
            PARAM_REBUILD_STATS["inner_bound_dropped"] += 1

    return face


def _try_make_closed_revolution_face(advanced_face: geo_su.AdvancedFace, face_surface):
    """Build a face that is *closed* in a parametric direction (full cylinder / cone /
    torus tube — signalled by a full-circle boundary edge) directly from the surface's
    parametric bounds, so OCC generates the seam itself. ``make_wire_from_face_bound``
    can't build a wire out of full-circle edges plus a doubled seam, which is why these
    faces (~5% of real-CAD curved faces — pipe walls, elbows) otherwise drop. Returns a
    face or None."""
    if not isinstance(face_surface, (Geom_CylindricalSurface, Geom_ConicalSurface, Geom_ToroidalSurface)):
        return None
    if not _has_full_circle_edge(advanced_face):
        return None
    face = _make_face_from_param_extent(advanced_face, face_surface)
    if face is not None:
        PARAM_REBUILD_STATS["closed_revolution_rebuilt"] += 1
    return face


_UV_FINITE_LIM = 1.0e50  # OCC marks an untrimmed natural bound as ±Precision::Infinite (~1e100)


def _face_uv_unbounded(face) -> bool:
    """True when the face still carries the surface's natural (infinite) parameter
    bounds — i.e. its boundary wire failed to trim the surface."""
    try:
        umin, umax, vmin, vmax = breptools.UVBounds(face)
    except Exception:  # noqa: BLE001 - treat an unqueryable face as unbounded
        return True
    return any((not math.isfinite(x)) or abs(x) > _UV_FINITE_LIM for x in (umin, umax, vmin, vmax))


def _shape_diag(shape) -> float:
    """Geometric bounding-box diagonal of any shape (no triangulation required)."""
    box = Bnd_Box()
    try:
        brepbndlib.Add(shape, box, False)
    except Exception:  # noqa: BLE001
        return math.inf
    if box.IsVoid():
        return 0.0
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return math.dist((xmin, ymin, zmin), (xmax, ymax, zmax))


# A trimmed face cannot legitimately extend far beyond its own outer boundary wire.
# 10x leaves generous slack for curvature bulge (a half-cylinder face's bbox is at
# most ~2x its wire's) while catching runaway trims that are orders of magnitude off.
_FACE_OVERRUN_FACTOR = 10.0


def _face_overruns_wire(face, wire_diag: float) -> bool:
    fd = _shape_diag(face)
    if not math.isfinite(fd):
        return True
    return fd > _FACE_OVERRUN_FACTOR * max(wire_diag, 1e-6)


# A built face whose extent dwarfs the WHOLE SOLID's topological vertices means a
# bad edge/pcurve evaluated the surface far from those vertices — a corrupt trim
# that BRepMesh then meshes into a runaway face (observed: a 15 cm solid whose face
# built out to 18 m, ratio ~126x; it renders as a giant flat "disk"). No single
# face can legitimately exceed its solid's overall vertex extent by much — even a
# closed cylinder/cone/torus face is bounded by the solid's radius — so this factor
# catches gross corruption with wide margin while leaving legit geometry untouched.
# Compared to the SOLID's vertices (not a single face's wire/bound, which a runaway
# edge inflates too, and which is degenerate for closed-revolution seam faces).
_FACE_VS_VERTEX_FACTOR = 8.0


def _cfs_vertex_diag(cfs_faces) -> float:
    """Bounding-box diagonal of the topological edge endpoints across a whole set of
    connected faces (i.e. the solid's vertex extent). 0.0 when no usable points."""
    pts: list = []
    for cf in cfs_faces or []:
        for fb in getattr(cf, "bounds", None) or []:
            el = getattr(getattr(fb, "bound", None), "edge_list", None) or []
            for oe in el:
                for p in (getattr(oe, "start", None), getattr(oe, "end", None)):
                    if p is None:
                        continue
                    try:
                        pts.append([float(z) for z in list(p)[:3]])
                    except Exception:  # noqa: BLE001 - non-point edge endpoint, ignore
                        pass
    if len(pts) < 2:
        return 0.0
    import numpy as _np

    a = _np.asarray(pts)
    return float(_np.linalg.norm(a.max(axis=0) - a.min(axis=0)))


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
        # Override knob:
        #   ADA_USE_SAT_PCURVES=false    skip SAT pcurves entirely → regen
        # (The older "attach pcurve via UpdateEdge with an affine knot remap"
        # path and its ADA_PCURVE_DRIVE_EDGE toggle were removed — the remap
        # mis-stretched shared pcurves into zero-area faces; the edge is now
        # always built directly from the pcurve, with shared pcurves trimmed.)
        import os as _os

        def _env_truthy(name: str, default: bool) -> bool:
            v = (_os.environ.get(name) or "").strip().lower()
            if v in {"1", "true", "yes", "on"}:
                return True
            if v in {"0", "false", "no", "off"}:
                return False
            return default

        use_pcurves = _env_truthy("ADA_USE_SAT_PCURVES", True)
        edge_list = getattr(face_bound.bound, "edge_list", None) or []
        occ_edges: list = []
        pcurves: list = []
        for oe in edge_list:
            supplied_pc = getattr(oe, "pcurve", None) if use_pcurves else None
            occ_edge = None
            # Build the edge directly from the SAT pcurve (3D derived as
            # surface(pcurve(t)) => 2D/3D consistent, shared pcurves trimmed to
            # the coedge). Edges this can't build fall to make_edge_from_edge +
            # the reproject path in update_edges_uv_gen.
            if supplied_pc is not None:
                occ_edge = _make_edge_from_pcurve(
                    supplied_pc,
                    face_surface,
                    edge_start=getattr(oe, "start", None),
                    edge_end=getattr(oe, "end", None),
                )
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
        try:
            outer_wire, n_updated, n_total = _build_bspline_wire(advanced_face.bounds[0], builder)
            if n_updated < n_total:
                # Any failed p-curve update on a BSpline-surface wire is a
                # crash trigger downstream — bail rather than feed the
                # half-attached wire into MakeFace/ShapeFix.
                raise UnableToCreateTesselationFromSolidOCCGeom(
                    f"p-curve update incomplete ({n_updated}/{n_total}); skipping degenerate BSpline face."
                )
        except UnableToCreateTesselationFromSolidOCCGeom as ex:
            # The trim wire couldn't be built from the bound edges (disconnected
            # or un-closeable even at relaxed tolerance — a frequent failure on
            # imported curved plates). Rather than drop the whole curved face and
            # lose most of the part's surface, build it from the surface's NATURAL
            # UV bounds. A B-spline is inherently bounded by its knot range, and a
            # plate's top/bottom B-spline spans the plate, so the untrimmed patch
            # recovers essentially the same surface (a genuinely-trimmed face
            # over-covers slightly — still far better than a missing face, which
            # is what dropped ~99% of some plates' area vs an external mesher).
            nat = BRepBuilderAPI_MakeFace(face_surface, 1e-6)
            if not nat.IsDone():
                raise
            PARAM_REBUILD_STATS["bspline_natural_bound"] += 1
            logger.debug("BSpline face wire build failed (%s); used natural surface bounds", ex)
            face = nat.Face()
            builder.UpdateFace(face, 1e-3)
            return face

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

    # A wire that fails to close in UV on an infinite revolution surface — e.g. a
    # cylinder/cone face whose boundary circles are split into arcs and the wire
    # crosses the parametric seam — leaves the face with the surface's NATURAL bounds:
    # infinite (or absurdly large) along the axis. BRepMesh then emits vertices
    # millions of units out, so a single such face explodes the whole model's bounding
    # box in the viewer. Detect it by comparing the face's geometric extent against
    # its own boundary wire (a trimmed face cannot legitimately overrun its boundary
    # by 10x) and rebuild from the boundary's projected parameter extent; if that
    # fails too, drop the face — a hole beats a kilometres-long sliver. The check is
    # gated to cylinder/cone: those are the only surfaces here with an infinite
    # direction (gp_Pln UV-projects exactly; sphere/torus/B-spline are bounded), and
    # the gate keeps the cost off the hot path. NOTE: ShapeFix_Face must NOT be used
    # to repair these — it segfaults on many real-world unbounded cone faces.
    if isinstance(face_surface, (Geom_CylindricalSurface, Geom_ConicalSurface)):
        wire_diag = _shape_diag(outer_wire)
        if _face_overruns_wire(face, wire_diag):
            rebuilt = _make_face_from_param_extent(advanced_face, face_surface)
            if rebuilt is None or _face_overruns_wire(rebuilt, wire_diag):
                raise UnableToCreateTesselationFromSolidOCCGeom(
                    f"unbounded face after wire trim on {type(advanced_face.face_surface).__name__}; skipping"
                )
            PARAM_REBUILD_STATS["unbounded_rebuilt"] += 1
            face = rebuilt

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


def make_surface_of_revolution_from_geom(sor: geo_su.SurfaceOfRevolution):
    """Build a Geom_SurfaceOfRevolution by revolving the generatrix curve about the axis.
    Currently supports a Line generatrix (the common cone/cylinder-from-revolution case)."""
    from OCC.Core.Geom import Geom_Line, Geom_SurfaceOfRevolution
    from OCC.Core.gp import gp_Ax1

    gen = sor.swept_curve
    if isinstance(gen, geo_cu.Line):
        generatrix = Geom_Line(gp_Pnt(*gen.pnt), gp_Dir(*gen.dir))
    else:
        raise NotImplementedError(f"SurfaceOfRevolution generatrix {type(gen)} not implemented")
    axis = gp_Ax1(gp_Pnt(*sor.axis_position.location), gp_Dir(*sor.axis_position.axis))
    return Geom_SurfaceOfRevolution(generatrix, axis)


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
    elif type(face_surface) is geo_su.SurfaceOfRevolution:
        return make_surface_of_revolution_from_geom(face_surface)
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
    # The solid's overall vertex extent — used to catch a corrupt face that BRepMesh
    # would blow up into a runaway "disk" far larger than the solid itself (see
    # _FACE_VS_VERTEX_FACTOR). cfs_faces may be a one-shot iterator, so materialise it.
    cfs_faces = list(cfs_faces)
    solid_vdiag = _cfs_vertex_diag(cfs_faces)
    runaway_limit = _FACE_VS_VERTEX_FACTOR * solid_vdiag if solid_vdiag > 1e-9 else math.inf

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

                # Drop a face whose built extent dwarfs the whole solid — a corrupt
                # trim that meshes into a metres-wide phantom. A missing small face
                # beats a runaway disk that wrecks the model's bounding box.
                fdiag = _shape_diag(face)
                if not math.isfinite(fdiag) or fdiag > runaway_limit:
                    PARAM_REBUILD_STATS["runaway_face_dropped"] += 1
                    raise UnableToCreateTesselationFromSolidOCCGeom(
                        f"face extent {fdiag:.1f} >> solid vertices {solid_vdiag:.3f} "
                        f"({type(cfs_face.face_surface).__name__}); corrupt trim, dropping face"
                    )

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

        # Handle plain Face with PolyLoop bounds (faceted-brep polygons)
        elif type(cfs_face) is geo_su.Face:
            n_faces += 1
            try:
                outer = cfs_face.bounds[0].bound
                if not isinstance(outer, PolyLoop):
                    raise NotImplementedError(f"Only PolyLoop bounds supported for Face, not {type(outer)}")
                face = make_face_from_poly_loop(outer)
                builder.UpdateFace(face, 1e-6)
                builder.Add(occ_shell, face)
            except Exception as ex:
                n_dropped += 1
                logger.warning("Skipping Face (PolyLoop): %s", ex)
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

    FACE_COVERAGE_STATS["total"] += n_faces
    FACE_COVERAGE_STATS["built"] += n_faces - n_dropped
    FACE_COVERAGE_STATS["dropped"] += n_dropped

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


def make_shell_from_polygonal_face_set_geom(pfs: geo_su.PolygonalFaceSet) -> TopoDS_Shape:
    """Build an IfcPolygonalFaceSet — a shared point list plus n-gon faces — into a sewn
    OCC shell. Each face is a planar polygon wire (1-based indices into the point list);
    sewing stitches the per-face shells along shared edges so a closed set becomes a solidable
    watertight shell."""
    coords = pfs.coordinates
    sewing = BRepBuilderAPI_Sewing(1e-6)
    n_faces = 0
    for face_idx in pfs.faces:
        poly = BRepBuilderAPI_MakePolygon()
        for i in face_idx:
            poly.Add(point3d(coords[i - 1]))
        poly.Close()
        if not poly.IsDone():
            logger.warning("PolygonalFaceSet: skipping face %s (could not build polygon wire)", face_idx)
            continue
        face_maker = BRepBuilderAPI_MakeFace(poly.Wire(), True)
        if not face_maker.IsDone():
            logger.warning("PolygonalFaceSet: skipping non-planar/degenerate face %s", face_idx)
            continue
        sewing.Add(face_maker.Face())
        n_faces += 1

    if n_faces == 0:
        raise UnableToCreateTesselationFromSolidOCCGeom("PolygonalFaceSet produced no usable faces")

    sewing.Perform()
    return sewing.SewedShape()


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
    #
    # Bound the GeomPlate cost. With OCC's defaults (NbPtsOnCur=15,
    # NbIter=2, MaxDeg=8, MaxSegments=9) a pathological boundary makes the
    # plate's curve-on-surface projection (ProjLib_CompProjectedCurve ->
    # math_NewtonFunctionSetRoot) grind for minutes on a single face — long
    # enough to blow the per-solid stream timeout and get the whole solid
    # *skipped*. This face is already a fallback (the exact ACIS/STEP
    # parameterisation is gone), so a coarse low-degree plate is plenty: it
    # only ever gets tessellated. Fewer projection points and a lower-degree
    # surface cut the dominant cost while still spanning the boundary; the
    # 120 s stream timeout remains the backstop for the rare residual hang.
    fill = BRepOffsetAPI_MakeFilling(
        2,  # Degree (default 3)
        6,  # NbPtsOnCur — points projected per constraint edge (default 15)
        1,  # NbIter — outer iterations (default 2)
        False,  # Anisotropie
        1.0e-4,  # Tol2d (default 1e-5)
        1.0e-3,  # Tol3d (default 1e-4)
        1.0e-2,  # TolAng
        1.0e-1,  # TolCurv
        3,  # MaxDeg — cap fitted-surface degree (default 8)
        4,  # MaxSegments — cap surface segments (default 9)
    )
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
