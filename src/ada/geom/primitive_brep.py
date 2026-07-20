"""Analytic B-rep shells for the CSG solid primitives (Sphere / Cone / ...).

The streaming STEP writer authors AP242 B-rep straight from ``ada.geom`` analytic
faces (Plane / Cylindrical / Conical / Spherical / Toroidal / B-spline surfaces),
but the CSG *primitive* solids — ``ada.geom.solids.Sphere``, ``.Cone`` — are not
shells, so they were dropped by the analytic path and (per the "no geometry left
behind" rule) must NOT fall back to a tessellated facet soup: a sphere has an
exact one-face spherical B-rep, a cone an exact conical-surface + planar-cap
B-rep.

This module builds those exact analytic shells kernel-free (pure Python, so it
runs under wasm/pyodide) as ``ada.geom`` :class:`ClosedShell`\\ s of
:class:`AdvancedFace`\\ s, which the existing ``_emit_analytic_brep`` /
``_brep_surface`` writer path then emits — and which the IFC face-surface writer
consumes too, so both emitters share one converter.

Box and Cylinder already emit as extrusions on the streaming path, so they are
intentionally not built here (the extrusion form is the proven watertight one).
"""

from __future__ import annotations

import dataclasses
import math
from bisect import bisect_right

import ada.geom.curves as cu
import ada.geom.solids as so
import ada.geom.surfaces as su
from ada.geom.direction import Direction
from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point


def _unit(v) -> tuple[float, float, float]:
    x, y, z = float(v[0]), float(v[1]), float(v[2])
    n = math.sqrt(x * x + y * y + z * z)
    if n == 0.0:
        return (0.0, 0.0, 1.0)
    return (x / n, y / n, z / n)


def _cross(a, b) -> tuple[float, float, float]:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _right_hand(axis) -> tuple[tuple, tuple]:
    """A ref (local x) + local y orthonormal to ``axis`` (local z)."""
    z = _unit(axis)
    seed = (1.0, 0.0, 0.0) if abs(z[0]) < 0.9 else (0.0, 1.0, 0.0)
    x = _unit(_cross(seed, z))
    y = _unit(_cross(z, x))
    return x, y


def _placement(location, axis, ref) -> Axis2Placement3D:
    return Axis2Placement3D(
        location=Point(*location),
        axis=Direction(*_unit(axis)),
        ref_direction=Direction(*_unit(ref)),
    )


def sphere_to_shell(sphere: so.Sphere) -> su.ClosedShell:
    """A whole sphere as one spherical ``AdvancedFace`` bounded by a single pole
    ``VertexLoop`` — the canonical fully-periodic-surface B-rep (matches the OCC /
    adacpp sphere: one SPHERICAL_SURFACE, one ADVANCED_FACE)."""
    center = (float(sphere.center[0]), float(sphere.center[1]), float(sphere.center[2]))
    r = float(sphere.radius)
    axis = (0.0, 0.0, 1.0)
    ref = (1.0, 0.0, 0.0)
    surface = su.SphericalSurface(position=_placement(center, axis, ref), radius=r)
    pole = Point(center[0], center[1], center[2] + r)
    face = su.AdvancedFace(
        bounds=[su.FaceBound(bound=cu.VertexLoop(loop_vertex=pole), orientation=True)],
        face_surface=surface,
        same_sense=True,
    )
    return su.ClosedShell(cfs_faces=[face])


def cone_to_shell(cone: so.Cone) -> su.ClosedShell | None:
    """A right circular cone as a conical lateral ``AdvancedFace`` (seam generatrix
    + base circle, split into two semicircle arcs so the shared base edge's sense is
    unambiguous) plus a planar bottom cap. Analytic — one CONICAL_SURFACE, one PLANE
    — never tessellated. Returns None for a degenerate cone."""
    r = float(cone.bottom_radius)
    h = float(cone.height)
    if r <= 0.0 or h <= 0.0:
        return None

    pos = cone.position
    base_c = (float(pos.location[0]), float(pos.location[1]), float(pos.location[2]))
    axis = _unit(pos.axis) if pos.axis is not None else (0.0, 0.0, 1.0)
    ref = _unit(pos.ref_direction) if pos.ref_direction is not None else _right_hand(axis)[0]

    apex = tuple(base_c[i] + h * axis[i] for i in range(3))
    p0 = tuple(base_c[i] + r * ref[i] for i in range(3))  # base circle at param 0 (+ref)
    p1 = tuple(base_c[i] - r * ref[i] for i in range(3))  # param pi (-ref)
    semi_angle = math.atan2(r, h)

    conical = su.ConicalSurface(position=_placement(base_c, axis, ref), radius=r, semi_angle=semi_angle)
    plane = su.Plane(position=_placement(base_c, axis, ref))
    circ = cu.Circle(position=_placement(base_c, axis, ref), radius=r)

    def _pt(p):
        return Point(*p)

    # shared edges (one EdgeCurve object each -> the writer shares the EDGE_CURVE and
    # derives each ORIENTED_EDGE's sense from the loop traversal direction):
    seam = cu.EdgeCurve(start=_pt(p0), end=_pt(apex), edge_geometry=None, same_sense=True)  # generatrix line
    arc_u = cu.EdgeCurve(start=_pt(p0), end=_pt(p1), edge_geometry=circ, same_sense=True)  # +y half
    arc_l = cu.EdgeCurve(start=_pt(p1), end=_pt(p0), edge_geometry=circ, same_sense=True)  # -y half

    def _oe(a, b, ec):
        return cu.OrientedEdge(start=_pt(a), end=_pt(b), edge_element=ec, orientation=True)

    # lateral face: down the seam, around the base (P0->P1->P0), back up the seam.
    lateral_loop = cu.EdgeLoop(
        edge_list=[_oe(apex, p0, seam), _oe(p0, p1, arc_u), _oe(p1, p0, arc_l), _oe(p0, apex, seam)]
    )
    lateral = su.AdvancedFace(
        bounds=[su.FaceBound(bound=lateral_loop, orientation=True)],
        face_surface=conical,
        same_sense=True,
    )
    # cap: the base circle traversed the opposite way, planar face flipped so its
    # normal points out of the solid (down the -axis).
    cap_loop = cu.EdgeLoop(edge_list=[_oe(p0, p1, arc_l), _oe(p1, p0, arc_u)])
    cap = su.AdvancedFace(
        bounds=[su.FaceBound(bound=cap_loop, orientation=True)],
        face_surface=plane,
        same_sense=False,
    )
    return su.ClosedShell(cfs_faces=[lateral, cap])


def primitive_to_analytic_shell(geometry) -> su.ClosedShell | None:
    """Pure-Python analytic B-rep shell for a CSG primitive solid, or None when the
    primitive has no kernel-free converter here (the caller then tries the adacpp
    native track). Kernel-free, so it works under wasm/pyodide."""
    if isinstance(geometry, so.Sphere):
        return sphere_to_shell(geometry)
    if isinstance(geometry, so.Cone):
        return cone_to_shell(geometry)
    return None


# Primitive solids the pure-Python track above cannot yet build a kernel-free
# analytic B-rep for, but which the CAD backend (adacpp / OCC) CAN build exactly —
# so the native track is worth trying before any faceting. Box/Cylinder/Sphere/Cone
# are covered by the extrusion or pure-Python paths, so this is Torus + friends.
_NATIVE_PRIMITIVES = (so.Torus, so.Cone, so.Sphere, so.Cylinder, so.Box)


def native_primitive_to_analytic_shell(geometry) -> su.ClosedShell | None:
    """Analytic B-rep shell for a CSG primitive built by the CAD backend (adacpp
    preferred, its bundled OCCT) and read back kernel-free as ``ada.geom`` analytic
    faces via the streaming STEP reader — so the emitted faces are exact analytic
    surfaces (CONICAL/SPHERICAL/TOROIDAL/PLANE), never a facet mesh. Best-effort:
    returns None when no backend is available, the build fails, or the reader yields
    no bounded analytic shell. Used only as a fallback after the pure-Python track."""
    if not isinstance(geometry, _NATIVE_PRIMITIVES):
        return None
    import tempfile

    from ada.geom import Geometry

    try:
        from ada.cad import active_backend
    except Exception:  # noqa: BLE001 - no CAD backend at all (slim worker)
        return None
    try:
        backend = active_backend()
    except Exception:  # noqa: BLE001
        return None
    build = getattr(backend, "build", None)
    write_step = getattr(backend, "write_step", None)
    if build is None or write_step is None:
        return None

    path = tempfile.mktemp(suffix=".stp")
    try:
        shape = build(Geometry(id="prim", geometry=geometry))
        write_step([shape], ["prim"], [(0.6, 0.6, 0.6)], path, "m", "AP242")
    except Exception:  # noqa: BLE001 - backend can't build/write this primitive
        return None

    from ada.cadit.step.read.stream_reader import stream_read_step

    shell = None
    try:
        for geom in stream_read_step(path, local_pool=False, tolerant=True):
            g = getattr(geom, "geometry", None)
            faces = getattr(g, "cfs_faces", None)
            # keep the first shell whose faces all carry a boundary the writer can
            # re-emit (a bound-less face — e.g. adacpp's whole-sphere VERTEX_LOOP the
            # reader drops — would be silently lost, so reject it here).
            if faces and all(getattr(f, "bounds", None) for f in faces):
                shell = g
                break
    except Exception:  # noqa: BLE001 - reader can't parse this STEP kernel-free
        return None
    finally:
        try:
            import os

            os.remove(path)
        except OSError:
            pass
    return shell


# ── extruded boundary-loop shells (analytic plate B-rep) ─────────────────────
def _vsub(a, b):
    return (float(a[0]) - float(b[0]), float(a[1]) - float(b[1]), float(a[2]) - float(b[2]))


def _vadd(a, b):
    return (float(a[0]) + float(b[0]), float(a[1]) + float(b[1]), float(a[2]) + float(b[2]))


def _vscale(a, s):
    return (float(a[0]) * s, float(a[1]) * s, float(a[2]) * s)


def _vdot(a, b):
    return float(a[0]) * float(b[0]) + float(a[1]) * float(b[1]) + float(a[2]) * float(b[2])


def _circumcenter(p1, m, p2):
    """Center of the circle through three 3D points, or None if collinear."""
    u = _vsub(m, p1)
    v = _vsub(p2, p1)
    w = _cross(u, v)
    w2 = _vdot(w, w)
    if w2 < 1e-24:
        return None
    # c = p1 + (|v|^2 (w x u) + |u|^2 (v x w)) / (2 |w|^2)
    t = _vadd(_vscale(_cross(w, u), _vdot(v, v)), _vscale(_cross(v, w), _vdot(u, u)))
    return _vadd(p1, _vscale(t, 0.5 / w2))


def _reversed_bspline(c: cu.BSplineCurveWithKnots) -> cu.BSplineCurveWithKnots:
    """Reverse a B-spline's parametrization (reverse control points/weights, mirror knots)."""
    total = c.knots[0] + c.knots[-1]
    common = dict(
        degree=c.degree,
        control_points_list=list(reversed(c.control_points_list)),
        curve_form=c.curve_form,
        closed_curve=c.closed_curve,
        self_intersect=c.self_intersect,
        knot_multiplicities=list(reversed(c.knot_multiplicities)),
        knots=[total - k for k in reversed(c.knots)],
        knot_spec=c.knot_spec,
    )
    if isinstance(c, cu.RationalBSplineCurveWithKnots):
        return cu.RationalBSplineCurveWithKnots(weights_data=list(reversed(c.weights_data)), **common)
    return cu.BSplineCurveWithKnots(**common)


def _translated_bspline(c: cu.BSplineCurveWithKnots, dvec) -> cu.BSplineCurveWithKnots:
    common = dict(
        degree=c.degree,
        control_points_list=[Point(*_vadd(p, dvec)) for p in c.control_points_list],
        curve_form=c.curve_form,
        closed_curve=c.closed_curve,
        self_intersect=c.self_intersect,
        knot_multiplicities=c.knot_multiplicities,
        knots=c.knots,
        knot_spec=c.knot_spec,
    )
    if isinstance(c, cu.RationalBSplineCurveWithKnots):
        return cu.RationalBSplineCurveWithKnots(weights_data=list(c.weights_data), **common)
    return cu.BSplineCurveWithKnots(**common)


def _extruded_bspline_surface(c: cu.BSplineCurveWithKnots, dvec) -> su.BSplineSurfaceWithKnots:
    """The EXACT linear extrusion of a B-spline curve: u follows the curve (same degree/knots),
    v is linear across the extrusion vector — two control rows per curve control point."""
    grid = [[Point(*p), Point(*_vadd(p, dvec))] for p in c.control_points_list]
    common = dict(
        u_degree=int(c.degree),
        v_degree=1,
        control_points_list=grid,
        surface_form=su.BSplineSurfaceForm.UNSPECIFIED,
        u_closed=bool(c.closed_curve),
        v_closed=False,
        self_intersect=False,
        u_multiplicities=list(c.knot_multiplicities),
        v_multiplicities=[2, 2],
        u_knots=list(c.knots),
        v_knots=[0.0, 1.0],
        knot_spec=c.knot_spec,
    )
    weights = getattr(c, "weights_data", None)
    if weights:
        return su.RationalBSplineSurfaceWithKnots(weights_data=[[float(w), float(w)] for w in weights], **common)
    return su.BSplineSurfaceWithKnots(**common)


def extruded_loop_to_shell(
    segments3d: list, extrude_dir, depth: float, base_offset: float = 0.0
) -> su.ClosedShell | None:
    """Analytic ``ClosedShell`` of ``AdvancedFace``s for a plate extruded from a boundary loop that
    carries analytic curved segments (``ArcSegment`` / ``SplineSegment``).

    ``IfcExtrudedAreaSolid`` cannot carry a B-spline boundary through the tools that matter —
    ``IfcIndexedPolyCurve`` is line/arc-only and ifcopenshell's engine won't build a wire from a
    B-spline ``IfcCompositeCurve`` segment — so a spline-boundary plate is emitted as an
    ``IfcAdvancedBrep`` instead: planar caps + planar/cylindrical side faces, and the spline side
    face as the EXACT degree-1-in-v B-spline surface of the linear extrusion. Topology mirrors the
    (OCC-round-trip-proven) streaming AP242 STEP emitter: shared base/top boundary edges, vertical
    connector edges, 4-edge quad side loops. Returns None for loops this builder cannot express.

    ``base_offset`` shifts the extrusion BASE by ``base_offset * extrude_dir`` from the modeled
    loop (the global thickness-anchor control; 0.0 keeps the historical output).
    """
    from ada.api.curves import ArcSegment, LineSegment, SplineSegment

    ez = _unit(extrude_dir)
    dvec = _vscale(ez, float(depth))
    ovec = _vscale(ez, float(base_offset))
    n = len(segments3d)
    if n < 3:
        return None

    # Normalize the loop to CCW about the extrusion axis so every face's same_sense is fixed.
    area2 = (0.0, 0.0, 0.0)
    for seg in segments3d:
        area2 = _vadd(area2, _cross(tuple(map(float, seg.p1[:3])), tuple(map(float, seg.p2[:3]))))
    if _vdot(area2, ez) < 0.0:
        rev = []
        for seg in reversed(segments3d):
            if isinstance(seg, SplineSegment):
                rev.append(SplineSegment(seg.p2, seg.p1, curve=_reversed_bspline(seg.curve)))
            elif isinstance(seg, ArcSegment):
                rev.append(ArcSegment(seg.p2, seg.p1, midpoint=seg.midpoint))
            else:
                rev.append(LineSegment(seg.p2, seg.p1))
        segments3d = rev

    base = [Point(*_vadd(tuple(map(float, seg.p1[:3])), ovec)) for seg in segments3d]
    top = [Point(*_vadd(b, dvec)) for b in base]

    def line_ec(pa: Point, pb: Point) -> cu.EdgeCurve:
        return cu.EdgeCurve(pa, pb, edge_geometry=cu.Line(pa, Direction(*_unit(_vsub(pb, pa)))), same_sense=True)

    # Vertical connector edges, shared between adjacent side faces.
    vert = [line_ec(base[i], top[i]) for i in range(n)]

    eb: list = [None] * n
    et: list = [None] * n
    faces: list[su.AdvancedFace] = []

    for i, seg in enumerate(segments3d):
        j = (i + 1) % n
        if isinstance(seg, SplineSegment):
            base_curve = _translated_bspline(seg.curve, ovec) if base_offset else seg.curve
            eb[i] = cu.EdgeCurve(base[i], base[j], edge_geometry=base_curve, same_sense=True)
            et[i] = cu.EdgeCurve(top[i], top[j], edge_geometry=_translated_bspline(base_curve, dvec), same_sense=True)
            surf = _extruded_bspline_surface(base_curve, dvec)
            same_sense = True  # CCW loop: du x dv = tangent x extrude = outward
        elif isinstance(seg, ArcSegment):
            c = _circumcenter(seg.p1, seg.midpoint, seg.p2)
            if c is None:
                return None
            r = math.sqrt(_vdot(_vsub(seg.p1, c), _vsub(seg.p1, c)))
            # Axis such that travelling CCW about it from p1 passes the midpoint before p2.
            axis = _unit(_cross(_vsub(seg.p1, c), _vsub(seg.midpoint, c)))
            ref = _unit(_vsub(seg.p1, c))
            c_base = _vadd(c, ovec)
            circle = cu.Circle(_placement(c_base, axis, ref), r)
            eb[i] = cu.EdgeCurve(base[i], base[j], edge_geometry=circle, same_sense=True)
            c_top = _vadd(c_base, dvec)
            et[i] = cu.EdgeCurve(
                top[i], top[j], edge_geometry=cu.Circle(_placement(c_top, axis, ref), r), same_sense=True
            )
            surf = su.CylindricalSurface(position=_placement(c_base, ez, ref), radius=r)
            # Cylinder normals point radially outward; a convex (outward-bulging) arc has its
            # material inside the circle, so radial == outward. Concave: flipped.
            chord_out = _cross(_unit(_vsub(seg.p2, seg.p1)), ez)
            same_sense = _vdot(_vsub(seg.midpoint, c), chord_out) > 0.0
        else:
            eb[i] = line_ec(base[i], base[j])
            et[i] = line_ec(top[i], top[j])
            tangent = _unit(_vsub(base[j], base[i]))
            out_n = _cross(tangent, ez)
            surf = su.Plane(position=_placement(base[i], out_n, tangent))
            same_sense = True

        loop = cu.EdgeLoop(
            edge_list=[
                cu.OrientedEdge(eb[i].start, eb[i].end, edge_element=eb[i], orientation=True),
                cu.OrientedEdge(vert[j].start, vert[j].end, edge_element=vert[j], orientation=True),
                cu.OrientedEdge(et[i].start, et[i].end, edge_element=et[i], orientation=False),
                cu.OrientedEdge(vert[i].start, vert[i].end, edge_element=vert[i], orientation=False),
            ]
        )
        faces.append(
            su.AdvancedFace(
                bounds=[su.FaceBound(bound=loop, orientation=True)], face_surface=surf, same_sense=same_sense
            )
        )

    xdir, _ = _right_hand(ez)
    top_loop = cu.EdgeLoop(edge_list=[cu.OrientedEdge(e.start, e.end, edge_element=e, orientation=True) for e in et])
    faces.append(
        su.AdvancedFace(
            bounds=[su.FaceBound(bound=top_loop, orientation=True)],
            face_surface=su.Plane(position=_placement(top[0], ez, xdir)),
            same_sense=True,
        )
    )
    bot_loop = cu.EdgeLoop(
        edge_list=[cu.OrientedEdge(e.start, e.end, edge_element=e, orientation=False) for e in reversed(eb)]
    )
    faces.append(
        su.AdvancedFace(
            bounds=[su.FaceBound(bound=bot_loop, orientation=True)],
            face_surface=su.Plane(position=_placement(base[0], _vscale(ez, -1.0), xdir)),
            same_sense=True,
        )
    )
    return su.ClosedShell(cfs_faces=faces)


# ── thickened curved-shell B-rep (PlateCurved thickness export) ──────────────
#
# A gxml/SAT curved shell carries only its reference face (typically a trimmed
# B-spline patch) plus a thickness ``t`` in the XML. ``face_to_thick_shell``
# turns that face into an analytic thickness-``t`` ``ClosedShell`` KERNEL-FREE:
# bottom = the reference surface (translated per the thickness anchor), top =
# the SAME surface translated by ``t * direction`` (exact — every B-spline
# control point / placement location moves rigidly), and one side face per
# boundary edge (plane / cylinder / ruled B-spline / surface-of-linear-
# extrusion). Topology follows ``extruded_loop_to_shell``: shared EdgeCurve
# objects between adjacent faces, 4-edge quad side loops, outward same_sense.
#
# NOTE: the translated top surface is a rigid copy, not a normal-offset — the
# wall thickness measured along the local surface normal varies with the
# surface's slope relative to ``direction``. For the gently curved hull shells
# this feeds (and for how Genie itself displays thickness) that is the intended
# semantic; a true offset surface is not expressible for the downstream writers.

THICKNESS_ANCHORS = ("as_is", "flipped", "centerline")


def thickness_anchor_base_offset(anchor: str, t: float) -> float:
    """Signed offset of the extrusion BASE from the modeled reference surface,
    along the (sense-corrected) thickness direction, for a given anchor.

    ``as_is``: base at the modeled surface, material on the +direction side (0).
    ``flipped``: material on the -direction side (base at ``-t``).
    ``centerline``: modeled surface is the mid-surface (base at ``-t/2``).
    """
    if anchor == "as_is":
        return 0.0
    if anchor == "flipped":
        return -float(t)
    if anchor == "centerline":
        return -0.5 * float(t)
    raise ValueError(f"unknown thickness anchor {anchor!r} (expected one of {THICKNESS_ANCHORS})")


def _is_zero_vec(v) -> bool:
    return float(v[0]) == 0.0 and float(v[1]) == 0.0 and float(v[2]) == 0.0


def _translated_placement(pos: Axis2Placement3D, dvec) -> Axis2Placement3D:
    return Axis2Placement3D(
        location=Point(*_vadd(pos.location, dvec)),
        axis=pos.axis,
        ref_direction=pos.ref_direction,
    )


def _translated_curve(c, dvec):
    """A rigid translated copy of a curve, or None when the type isn't supported.
    Returns the original object for a zero vector (instance sharing is deliberate)."""
    if _is_zero_vec(dvec):
        return c
    if isinstance(c, cu.BSplineCurveWithKnots):  # incl. Rational subclass
        return _translated_bspline(c, dvec)
    if isinstance(c, cu.Line):
        return dataclasses.replace(c, pnt=Point(*_vadd(c.pnt, dvec)))
    if isinstance(c, (cu.Circle, cu.Ellipse)):
        return dataclasses.replace(c, position=_translated_placement(c.position, dvec))
    if isinstance(c, cu.PolyLine):
        return dataclasses.replace(c, points=[Point(*_vadd(p, dvec)) for p in c.points])
    if isinstance(c, cu.TrimmedCurve):
        basis = _translated_curve(c.basis_curve, dvec)
        if basis is None:
            return None
        trims = []
        for trim in (c.trim1, c.trim2):
            trims.append(Point(*_vadd(trim, dvec)) if isinstance(trim, Point) else trim)
        return dataclasses.replace(c, basis_curve=basis, trim1=trims[0], trim2=trims[1])
    if isinstance(c, cu.SurfaceCurve):
        c3d = _translated_curve(c.curve_3d, dvec)
        if c3d is None:
            return None
        # pcurves are UV-space images; a rigid translation of surface + curve keeps them valid.
        return dataclasses.replace(c, curve_3d=c3d)
    return None


def _translated_surface(s, dvec):
    """A rigid translated copy of a surface, or None when the type isn't supported."""
    if _is_zero_vec(dvec):
        return s
    if isinstance(s, su.BSplineSurfaceWithKnots):  # incl. Rational subclass (weights copied by replace)
        return dataclasses.replace(
            s, control_points_list=[[Point(*_vadd(p, dvec)) for p in row] for row in s.control_points_list]
        )
    if isinstance(s, (su.Plane, su.CylindricalSurface, su.ConicalSurface, su.SphericalSurface, su.ToroidalSurface)):
        return dataclasses.replace(s, position=_translated_placement(s.position, dvec))
    return None


# -- kernel-free B-spline surface evaluation (normal probe) --------------------
def _full_knots(knots, mults) -> list[float]:
    return [float(k) for k, m in zip(knots, mults) for _ in range(int(m))]


def _deboor_pt(x: float, knots: list[float], cps: list[list[float]], deg: int) -> list[float]:
    """de Boor point evaluation on a full (repeated) knot vector; pure Python."""
    k = bisect_right(knots, x) - 1
    k = max(deg, min(k, len(cps) - 1))
    d = [list(cps[j + k - deg]) for j in range(deg + 1)]
    for r in range(1, deg + 1):
        for j in range(deg, r - 1, -1):
            lo = knots[j + k - deg]
            hi = knots[j + 1 + k - r]
            a = 0.0 if hi == lo else (x - lo) / (hi - lo)
            d[j] = [(1.0 - a) * p + a * q for p, q in zip(d[j - 1], d[j])]
    return d[deg]


def _bspline_surface_point(s: su.BSplineSurfaceWithKnots, u: float, v: float) -> tuple[float, float, float]:
    """Exact point on a (rational) B-spline surface via tensor-product de Boor."""
    uk = _full_knots(s.u_knots, s.u_multiplicities)
    vk = _full_knots(s.v_knots, s.v_multiplicities)
    weights = getattr(s, "weights_data", None)
    rows_h = []
    for i, row in enumerate(s.control_points_list):
        hrow = []
        for j, p in enumerate(row):
            w = float(weights[i][j]) if weights else 1.0
            hrow.append([float(p[0]) * w, float(p[1]) * w, float(p[2]) * w, w])
        rows_h.append(hrow)
    # de Boor along v for each u-row, then along u across the results.
    col = [_deboor_pt(v, vk, hrow, int(s.v_degree)) for hrow in rows_h]
    r = _deboor_pt(u, uk, col, int(s.u_degree))
    w = r[3] if r[3] != 0.0 else 1.0
    return (r[0] / w, r[1] / w, r[2] / w)


def _bspline_mid_normal(s: su.BSplineSurfaceWithKnots) -> tuple[float, float, float] | None:
    """Surface normal at the parametric mid-point (central differences), or None if degenerate."""
    uk = _full_knots(s.u_knots, s.u_multiplicities)
    vk = _full_knots(s.v_knots, s.v_multiplicities)
    du_deg, dv_deg = int(s.u_degree), int(s.v_degree)
    u0, u1 = uk[du_deg], uk[len(uk) - du_deg - 1]
    v0, v1 = vk[dv_deg], vk[len(vk) - dv_deg - 1]
    if u1 <= u0 or v1 <= v0:
        return None
    # Probe at the middle and, if degenerate there, at an off-centre fallback.
    for fu, fv in ((0.5, 0.5), (0.35, 0.65), (0.65, 0.35)):
        um, vm = u0 + fu * (u1 - u0), v0 + fv * (v1 - v0)
        eu, ev = 1e-4 * (u1 - u0), 1e-4 * (v1 - v0)
        pu1 = _bspline_surface_point(s, min(um + eu, u1), vm)
        pu0 = _bspline_surface_point(s, max(um - eu, u0), vm)
        pv1 = _bspline_surface_point(s, um, min(vm + ev, v1))
        pv0 = _bspline_surface_point(s, um, max(vm - ev, v0))
        n = _cross(_vsub(pu1, pu0), _vsub(pv1, pv0))
        if _vdot(n, n) > 1e-30:
            return _unit(n)
    return None


def face_mid_normal(face: su.AdvancedFace | su.FaceSurface) -> tuple[float, float, float] | None:
    """The face's own oriented normal (surface normal at a representative parameter,
    flipped by ``same_sense``), or None when the surface type has no cheap kernel-free
    probe (conical/spherical/toroidal etc. — the caller falls back to the bare face)."""
    s = face.face_surface
    n = None
    if isinstance(s, su.Plane):
        n = _unit(s.position.axis) if s.position.axis is not None else (0.0, 0.0, 1.0)
    elif isinstance(s, su.BSplineSurfaceWithKnots):
        try:
            n = _bspline_mid_normal(s)
        except Exception:  # noqa: BLE001 - malformed knot/cp data -> no thickening
            n = None
    if n is None:
        return None
    if not face.same_sense:
        n = _vscale(n, -1.0)
    return n


def _circle_arc_bspline(circle: cu.Circle, p_start, p_end, same_sense: bool) -> cu.RationalBSplineCurveWithKnots | None:
    """The EXACT rational quadratic B-spline of the circular arc from ``p_start`` to
    ``p_end`` along the circle's parametric direction (reversed when ``same_sense`` is
    False) — the standard piecewise-Bezier conic (<=90 deg segments, w = cos(half-angle)).

    Returns None for a degenerate frame or a (near-)full circle, where the arc between
    two coincident endpoints is ambiguous — callers keep the periodic surface form then.
    """
    pos = circle.position
    if pos.axis is None:
        return None
    z = _unit(pos.axis)
    x = _unit(pos.ref_direction) if pos.ref_direction is not None else _right_hand(z)[0]
    y = _cross(z, x)
    c = tuple(float(v) for v in pos.location)
    r = float(circle.radius)
    if r <= 0.0:
        return None

    def _theta(p):
        d = _vsub(p, c)
        return math.atan2(_vdot(d, y), _vdot(d, x))

    th_s, th_e = _theta(p_start), _theta(p_end)
    if same_sense:
        sweep = (th_e - th_s) % (2.0 * math.pi)
    else:
        sweep = -((th_s - th_e) % (2.0 * math.pi))
    if abs(sweep) < 1e-9 or abs(sweep) > 2.0 * math.pi - 1e-9:
        return None  # coincident endpoints: ambiguous (full circle) — keep the periodic surface

    n_seg = max(1, int(math.ceil(abs(sweep) / (0.5 * math.pi))))
    delta = sweep / n_seg
    half = 0.5 * abs(delta)
    w_mid = math.cos(half)
    if w_mid < 1e-9:
        return None

    def _pt(theta, rad=r):
        return Point(*(c[i] + rad * (math.cos(theta) * x[i] + math.sin(theta) * y[i]) for i in range(3)))

    cps: list[Point] = [_pt(th_s)]
    weights: list[float] = [1.0]
    for k in range(n_seg):
        a0 = th_s + k * delta
        mid = a0 + 0.5 * delta
        cps.append(_pt(mid, rad=r / w_mid))
        cps.append(_pt(a0 + delta))
        weights.extend([w_mid, 1.0])

    return cu.RationalBSplineCurveWithKnots(
        degree=2,
        control_points_list=cps,
        curve_form=cu.BSplineCurveFormEnum.UNSPECIFIED,
        closed_curve=False,
        self_intersect=False,
        knot_multiplicities=[3] + [2] * (n_seg - 1) + [3],
        knots=[k / n_seg for k in range(n_seg + 1)],
        knot_spec=cu.KnotType.UNSPECIFIED,
        weights_data=weights,
    )


def _vkey(p) -> tuple[float, float, float]:
    return (round(float(p[0]), 9), round(float(p[1]), 9), round(float(p[2]), 9))


def _line_geom(a: Point, b: Point) -> cu.Line | None:
    d = _vsub(b, a)
    if _vdot(d, d) < 1e-24:
        return None
    return cu.Line(a, Direction(*_unit(d)))


def face_to_thick_shell(
    advanced_face: su.AdvancedFace | su.FaceSurface,
    direction,
    t: float,
    anchor: str = "as_is",
    direction_agrees_with_face: bool = True,
) -> su.ClosedShell | None:
    """Thickness-``t`` analytic ``ClosedShell`` for a bounded face, kernel-free.

    ``direction`` is the shell's sense-corrected thickness direction (unit); material
    spans the anchor-dependent slab along it (see :func:`thickness_anchor_base_offset`).
    ``direction_agrees_with_face`` says whether ``direction`` equals the face's own
    oriented normal (surface normal x same_sense) — for a gxml ``curved_shell`` this is
    the authored ``sense_flag``.

    Faces: bottom/top = rigid translated copies of the input face (outward normals
    -direction / +direction), one side face per boundary edge of every loop (outer AND
    hole loops — a hole grows an inner side tube automatically). Shared EdgeCurve /
    vertex objects follow the ``extruded_loop_to_shell`` conventions so downstream
    writers keep the shell topologically closed. Returns None for anything unbuildable
    (unsupported surface/curve type, degenerate edge/loop) — callers keep today's bare
    face, never lose geometry.
    """
    t = float(t)
    if t <= 0.0:
        return None
    bounds = getattr(advanced_face, "bounds", None)
    if not bounds:
        return None
    for fb in bounds:
        if not isinstance(fb, su.FaceBound) or not isinstance(fb.bound, cu.EdgeLoop):
            return None
        if not fb.bound.edge_list:
            return None
        for oe in fb.bound.edge_list:
            if not isinstance(oe, cu.OrientedEdge) or not isinstance(oe.edge_element, cu.EdgeCurve):
                return None

    dirn = _unit(direction)
    base_off = thickness_anchor_base_offset(anchor, t)
    ovec = _vscale(dirn, base_off)
    tvec = _vscale(dirn, t)
    topvec = _vadd(ovec, tvec)
    agree = bool(direction_agrees_with_face)

    surf_bot = _translated_surface(advanced_face.face_surface, ovec)
    surf_top = _translated_surface(advanced_face.face_surface, topvec)
    if surf_bot is None or surf_top is None:
        return None

    def _edge_copy(ec: cu.EdgeCurve, dvec, memo: dict):
        hit = memo.get(id(ec))
        if hit is not None:
            return hit
        if _is_zero_vec(dvec):
            memo[id(ec)] = ec
            return ec
        geom = _translated_curve(ec.edge_geometry, dvec) if ec.edge_geometry is not None else None
        if ec.edge_geometry is not None and geom is None:
            return None  # untranslatable curve type -> shell unbuildable
        copy = cu.EdgeCurve(
            Point(*_vadd(ec.start, dvec)), Point(*_vadd(ec.end, dvec)), edge_geometry=geom, same_sense=ec.same_sense
        )
        memo[id(ec)] = copy
        return copy

    memo_bot: dict = {}
    memo_top: dict = {}

    def _face_at(surface, offset_vec, memo, flip: bool) -> su.AdvancedFace | None:
        new_bounds = []
        for fb in bounds:
            oes = []
            edge_list = fb.bound.edge_list if not flip else list(reversed(fb.bound.edge_list))
            for oe in edge_list:
                ec = _edge_copy(oe.edge_element, offset_vec, memo)
                if ec is None:
                    return None
                orientation = bool(oe.orientation) if not flip else not oe.orientation
                a, b = (ec.start, ec.end) if orientation else (ec.end, ec.start)
                oes.append(
                    cu.OrientedEdge(
                        a,
                        b,
                        edge_element=ec,
                        orientation=orientation,
                        pcurve=oe.pcurve,
                        t_start=oe.t_start,
                        t_end=oe.t_end,
                    )
                )
            new_bounds.append(su.FaceBound(bound=cu.EdgeLoop(edge_list=oes), orientation=fb.orientation))
        same_sense = bool(advanced_face.same_sense) if not flip else not advanced_face.same_sense
        return su.AdvancedFace(bounds=new_bounds, face_surface=surface, same_sense=same_sense)

    # Bottom face's outward normal is -direction, top's +direction. The input face's
    # oriented normal is +direction when ``agree`` — so bottom flips iff agree.
    bot_face = _face_at(surf_bot, ovec, memo_bot, flip=agree)
    top_face = _face_at(surf_top, topvec, memo_top, flip=not agree)
    if bot_face is None or top_face is None:
        return None

    faces: list[su.AdvancedFace] = [bot_face, top_face]
    connectors: dict = {}

    def _connector(key, p_bot: Point, p_top: Point) -> cu.EdgeCurve:
        conn = connectors.get(key)
        if conn is None:
            conn = cu.EdgeCurve(p_bot, p_top, edge_geometry=cu.Line(p_bot, Direction(*dirn)), same_sense=True)
            connectors[key] = conn
        return conn

    for fb in bounds:
        items = [(oe, oe.edge_element, bool(oe.orientation)) for oe in fb.bound.edge_list]
        # The loop as listed with orientation=True is CCW about the FACE normal; the side
        # faces are built around a loop that is CCW about +direction.
        if bool(fb.orientation) != agree:
            items = [(oe, ec, not fwd) for (oe, ec, fwd) in reversed(items)]
        for src_oe, ec, fwd in items:
            bec = _edge_copy(ec, ovec, memo_bot)
            tec = _edge_copy(ec, topvec, memo_top)
            if bec is None or tec is None:
                return None
            a_b, b_b = (bec.start, bec.end) if fwd else (bec.end, bec.start)
            a_t, b_t = (tec.start, tec.end) if fwd else (tec.end, tec.start)
            ka = _vkey(ec.start if fwd else ec.end)
            kb = _vkey(ec.end if fwd else ec.start)
            conn_a = _connector(ka, a_b, a_t)
            conn_b = _connector(kb, b_b, b_t)

            geom0 = ec.edge_geometry
            bgeom = bec.edge_geometry
            if isinstance(geom0, cu.SurfaceCurve):
                geom0 = geom0.curve_3d
            if isinstance(bgeom, cu.SurfaceCurve):
                bgeom = bgeom.curve_3d

            straight = (
                geom0 is None
                or isinstance(geom0, cu.Line)
                or (isinstance(geom0, cu.PolyLine) and len(geom0.points) == 2)
            )
            # Does the traversal follow the underlying curve's own parametric direction?
            # (fwd = traversal from edge start to edge end; same_sense = the curve runs
            # start->end.) The swept side surfaces below have du = the curve's parametric
            # tangent, so their same_sense is anchored to this, not to fwd alone.
            along_param = fwd == bool(ec.same_sense)

            arc = None
            if isinstance(geom0, cu.Circle):
                # EXACT rational-B-spline ruled surface of the ARC between the edge's
                # vertices. Preferred over CylindricalSurface / SurfaceOfLinearExtrusion
                # because the periodic forms are untrimmable for the stream tessellation
                # kernel (a boundary arc's side face then meshes as the FULL tube); the
                # arc patch's natural bounds ARE the ribbon, so it is robust everywhere.
                arc = _circle_arc_bspline(bgeom, bec.start, bec.end, bool(bec.same_sense))

            if straight:
                chord = _vsub(b_b, a_b)
                if _vdot(chord, chord) < 1e-24:
                    return None  # degenerate straight edge
                tangent = _unit(chord)
                out_n = _cross(tangent, dirn)
                if _vdot(out_n, out_n) < 1e-24:
                    return None  # edge parallel to the thickness direction
                surf = su.Plane(position=_placement(a_b, out_n, tangent))
                ssense = True
            elif isinstance(geom0, cu.BSplineCurveWithKnots):
                # Exact ruled surface of the linear extrusion (degree 1 in v).
                surf = _extruded_bspline_surface(bgeom, tvec)
                ssense = along_param
            elif arc is not None:
                # The arc curve already runs start->end (same_sense folded in), so its
                # parametric tangent follows the edge direction.
                surf = _extruded_bspline_surface(arc, tvec)
                ssense = fwd
            elif (
                isinstance(geom0, cu.Circle)
                and geom0.position.axis is not None
                and abs(_vdot(_unit(geom0.position.axis), dirn)) > 1.0 - 1e-9
            ):
                # Full-circle edge (coincident endpoints) with axis parallel to the
                # thickness direction: the natural cylinder IS the side face.
                dp = _vdot(_unit(geom0.position.axis), dirn)
                ref = bgeom.position.ref_direction
                ref = _unit(ref) if ref is not None else _right_hand(dirn)[0]
                surf = su.CylindricalSurface(
                    position=_placement(bgeom.position.location, dirn, ref), radius=float(geom0.radius)
                )
                # Cylinder normals point radially outward: outward-of-solid equals radial
                # when the traversal follows the circle's own (CCW about its axis)
                # parametrization AND the axis points along +direction.
                ssense = along_param if dp > 0.0 else not along_param
            else:
                # Generic swept side face (ellipse / full circle off-axis / multi-segment
                # polyline / trimmed curve).
                surf = su.SurfaceOfLinearExtrusion(
                    swept_curve=bgeom, position=None, extrusion_direction=Direction(*dirn), depth=t
                )
                ssense = along_param

            # The source coedge's parametric trim rides along: the translated copies keep the
            # original curve's parametrization exactly, and without (t_start, t_end) a CLOSED
            # edge geometry (a boundary arc on a full Circle) is untrimmable for the stream
            # kernel — it would tessellate the side face over the whole cylinder.
            ts, te = src_oe.t_start, src_oe.t_end
            loop = cu.EdgeLoop(
                edge_list=[
                    cu.OrientedEdge(a_b, b_b, edge_element=bec, orientation=fwd, t_start=ts, t_end=te),
                    cu.OrientedEdge(b_b, b_t, edge_element=conn_b, orientation=True),
                    cu.OrientedEdge(b_t, a_t, edge_element=tec, orientation=not fwd, t_start=ts, t_end=te),
                    cu.OrientedEdge(a_t, a_b, edge_element=conn_a, orientation=False),
                ]
            )
            faces.append(
                su.AdvancedFace(
                    bounds=[su.FaceBound(bound=loop, orientation=True)], face_surface=surf, same_sense=ssense
                )
            )

    return su.ClosedShell(cfs_faces=faces)
