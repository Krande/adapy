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
