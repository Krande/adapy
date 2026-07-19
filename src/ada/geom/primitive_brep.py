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

import math

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


def extruded_loop_to_shell(segments3d: list, extrude_dir, depth: float) -> su.ClosedShell | None:
    """Analytic ``ClosedShell`` of ``AdvancedFace``s for a plate extruded from a boundary loop that
    carries analytic curved segments (``ArcSegment`` / ``SplineSegment``).

    ``IfcExtrudedAreaSolid`` cannot carry a B-spline boundary through the tools that matter —
    ``IfcIndexedPolyCurve`` is line/arc-only and ifcopenshell's engine won't build a wire from a
    B-spline ``IfcCompositeCurve`` segment — so a spline-boundary plate is emitted as an
    ``IfcAdvancedBrep`` instead: planar caps + planar/cylindrical side faces, and the spline side
    face as the EXACT degree-1-in-v B-spline surface of the linear extrusion. Topology mirrors the
    (OCC-round-trip-proven) streaming AP242 STEP emitter: shared base/top boundary edges, vertical
    connector edges, 4-edge quad side loops. Returns None for loops this builder cannot express.
    """
    from ada.api.curves import ArcSegment, LineSegment, SplineSegment

    ez = _unit(extrude_dir)
    dvec = _vscale(ez, float(depth))
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

    base = [Point(*tuple(map(float, seg.p1[:3]))) for seg in segments3d]
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
            eb[i] = cu.EdgeCurve(base[i], base[j], edge_geometry=seg.curve, same_sense=True)
            et[i] = cu.EdgeCurve(top[i], top[j], edge_geometry=_translated_bspline(seg.curve, dvec), same_sense=True)
            surf = _extruded_bspline_surface(seg.curve, dvec)
            same_sense = True  # CCW loop: du x dv = tangent x extrude = outward
        elif isinstance(seg, ArcSegment):
            c = _circumcenter(seg.p1, seg.midpoint, seg.p2)
            if c is None:
                return None
            r = math.sqrt(_vdot(_vsub(seg.p1, c), _vsub(seg.p1, c)))
            # Axis such that travelling CCW about it from p1 passes the midpoint before p2.
            axis = _unit(_cross(_vsub(seg.p1, c), _vsub(seg.midpoint, c)))
            ref = _unit(_vsub(seg.p1, c))
            circle = cu.Circle(_placement(c, axis, ref), r)
            eb[i] = cu.EdgeCurve(base[i], base[j], edge_geometry=circle, same_sense=True)
            c_top = _vadd(c, dvec)
            et[i] = cu.EdgeCurve(
                top[i], top[j], edge_geometry=cu.Circle(_placement(c_top, axis, ref), r), same_sense=True
            )
            surf = su.CylindricalSurface(position=_placement(c, ez, ref), radius=r)
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
