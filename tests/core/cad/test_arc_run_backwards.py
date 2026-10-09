"""An arc whose edge runs against its circle is the arc from t_start down to t_end, on either kernel.

An edge on a circle carries its trims on the circle's own parameter, ``t_start`` where the loop
enters it and ``t_end`` where it leaves; run against the circle (``same_sense`` false) the first
is the larger. pythonocc's ``BRepBuilderAPI_MakeEdge(circ, p1, p2)`` takes p1 > p2 as that same
arc reversed. adacpp's record took it as a trim on a periodic curve and went the long way round,
from p1 up to p2 + 2 pi -- measured on ada-cpp 0.31.1 with GeniE's quarter cylinder
(``cylinder_shell``, whose top arc runs pi/2 -> 0): an invalid face of 3 pi instead of pi
(Krande/adapy#435).
"""

from __future__ import annotations

import numpy as np
import pytest

from ada.geom import Geometry
from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su
from ada.geom.points import Point


def _edge(start, end, curve, same_sense=True, t=(None, None)) -> geo_cu.OrientedEdge:
    a, b = Point(*start), Point(*end)
    ec = geo_cu.EdgeCurve(start=a, end=b, edge_geometry=curve, same_sense=same_sense)
    return geo_cu.OrientedEdge(start=a, end=b, edge_element=ec, orientation=True, t_start=t[0], t_end=t[1])


def _line(p1, p2) -> geo_cu.OrientedEdge:
    return _edge(
        p1, p2, geo_cu.Line(Point(*p1), np.subtract(p2, p1)), t=(0.0, float(np.linalg.norm(np.subtract(p2, p1))))
    )


def _circle(z: float, r: float = 1.0) -> geo_cu.Circle:
    return geo_cu.Circle(geo_su.Axis2Placement3D(location=Point(0, 0, z), axis=(0, 0, 1), ref_direction=(1, 0, 0)), r)


def _area_and_validity(backend, face):
    shape = backend.build(Geometry(1, face, None))
    return backend.area(shape), backend.is_valid(shape)


def _reversed(loop):
    return [
        _edge(oe.end, oe.start, oe.edge_element.edge_geometry, not oe.edge_element.same_sense, (oe.t_end, oe.t_start))
        for oe in reversed(loop)
    ]


@pytest.mark.parametrize("turn", [0.5 * np.pi, 1.5 * np.pi])
@pytest.mark.parametrize("same_sense", [True, False])
@pytest.mark.parametrize("run", ["as GeniE wrote it", "the other way"])
def test_a_cylinder_shell_whose_top_arc_runs_backwards(backend, run, same_sense, turn):
    """GeniE's ``cylinder_shell`` as the reader gives it: radius 1 about +z, height 2, and a 3/4 turn of it.

    Either way round and either face sense, the face is the quarter. Measured on ada-cpp 0.31.1:
    its cylinder builder takes the loop as running clockwise in (angle, height) and does not
    turn it round -- this outline, counter-clockwise there, built invalid at -pi once its arc
    was built from t_start down to t_end; pythonocc builds it valid whichever way it runs.
    """
    a0, a1 = np.pi / 2, np.pi / 2 + turn  # the three-quarter shell runs through the angle +-pi
    p0, p1 = (np.cos(a0), np.sin(a0)), (np.cos(a1), np.sin(a1))
    loop = [
        _edge((*p0, 0), (*p1, 0), _circle(0.0), t=(a0, a1)),
        _line((*p1, 0), (*p1, 2)),
        _edge((*p1, 2), (*p0, 2), _circle(2.0), same_sense=False, t=(a1, a0)),
        _line((*p0, 2), (*p0, 0)),
    ]
    if run == "the other way":
        loop = _reversed(loop)
    surface = geo_su.CylindricalSurface(
        geo_su.Axis2Placement3D(location=Point(0, 0, 0), axis=(0, 0, 1), ref_direction=(1, 0, 0)), 1.0
    )
    face = geo_su.AdvancedFace(
        bounds=[geo_su.FaceBound(geo_cu.EdgeLoop(loop), True)], face_surface=surface, same_sense=same_sense
    )
    area, valid = _area_and_validity(backend, face)
    assert valid
    assert area == pytest.approx(2 * turn, rel=1e-12)


def _half_nurbs(z: float) -> geo_cu.RationalBSplineCurveWithKnots:
    """The half circle angle 0 -> pi at height z, as GeniE writes an arc (an ``intcurve``): rational, degree 2."""
    w = np.sqrt(0.5)
    return geo_cu.RationalBSplineCurveWithKnots(
        degree=2,
        control_points_list=[Point(1, 0, z), Point(1, 1, z), Point(0, 1, z), Point(-1, 1, z), Point(-1, 0, z)],
        curve_form=geo_cu.BSplineCurveFormEnum.CIRCULAR_ARC,
        closed_curve=False,
        self_intersect=False,
        knot_multiplicities=[3, 2, 3],
        knots=[0.0, np.pi / 2, np.pi],
        knot_spec=geo_cu.KnotType.UNSPECIFIED,
        weights_data=[1.0, w, 1.0, w, 1.0],
    )


#: The slope of the plane the ellipse edges lie in: z = z0 + SLOPE * y.
SLOPE = 0.75


def _half_ellipse(z: float) -> geo_cu.Ellipse:
    """Where the plane z = z0 + SLOPE y cuts the cylinder: angle theta at parameter theta - pi/2."""
    k = float(np.hypot(1.0, SLOPE))
    position = geo_su.Axis2Placement3D(
        location=Point(0, 0, z), axis=(0, -SLOPE / k, 1 / k), ref_direction=(0, 1 / k, SLOPE / k)
    )
    return geo_cu.Ellipse(position, k, 1.0)


@pytest.mark.parametrize("run", ["counter-clockwise", "clockwise"])
@pytest.mark.parametrize("kind", ["nurbs", "ellipse"])
def test_a_half_cylinder_bounded_by_other_curves(backend, kind, run, request):
    """A half cylinder (angle 0 -> pi, height 2, radius 1) whose arcs are GeniE's rational B-splines or ellipses.

    The direction a cylindrical loop runs was measured from its circle edges alone; a B-spline or
    an ellipse gave its start point only, so a half turn was one step and its sign a guess -- on
    adacpp the B-spline band built valid clockwise before that, and invalid both ways after it.
    The region is the band whatever way round its outline runs: 2 pi, as pythonocc builds the
    B-spline one.
    """
    if kind == "ellipse" and backend.name == "pythonocc-core":
        request.applymarker(
            pytest.mark.xfail(
                strict=True,
                reason="pythonocc's cylinder face from slanted ellipse edges overruns its wire and is rebuilt "
                "from the boundary's (u, v) extent: measured 8.639 = pi x (2 + 0.75), the band's bounding "
                "rectangle, not 2 pi",
            )
        )
    if kind == "nurbs":
        lower, upper, t = _half_nurbs(0.0), _half_nurbs(2.0), (0.0, np.pi)
        ends = [(1.0, 0.0, 0.0), (-1.0, 0.0, 0.0)]
    else:
        lower, upper, t = _half_ellipse(0.0), _half_ellipse(2.0), (-np.pi / 2, np.pi / 2)
        ends = [(1.0, 0.0, 0.0), (-1.0, 0.0, 0.0)]
    (x0, y0, _), (x1, y1, _) = ends
    loop = [
        _edge((x0, y0, 0), (x1, y1, 0), lower, t=t),
        _line((x1, y1, 0), (x1, y1, 2)),
        _edge((x1, y1, 2), (x0, y0, 2), upper, same_sense=False, t=t[::-1]),
        _line((x0, y0, 2), (x0, y0, 0)),
    ]
    if run == "clockwise":
        loop = _reversed(loop)
    surface = geo_su.CylindricalSurface(
        geo_su.Axis2Placement3D(location=Point(0, 0, 0), axis=(0, 0, 1), ref_direction=(1, 0, 0)), 1.0
    )
    face = geo_su.AdvancedFace(
        bounds=[geo_su.FaceBound(geo_cu.EdgeLoop(loop), True)], face_surface=surface, same_sense=True
    )
    area, valid = _area_and_validity(backend, face)
    assert valid
    assert area == pytest.approx(2 * np.pi, rel=1e-12)


@pytest.mark.parametrize("turn", [0.5 * np.pi, 1.5 * np.pi])
@pytest.mark.parametrize("ref_angle", [0.0, np.pi / 2, 2.5])
def test_a_sector_whose_arc_runs_backwards(backend, ref_angle, turn):
    """A sector from +x clockwise by ``turn`` (a quarter, three quarters), its arc run backwards.

    The trims are on the circle's own parameter, measured from its ref direction: a ref turned by
    ``ref_angle`` moves them by as much, and a builder that measured from a direction of its own
    would trim the wrong arc (pythonocc's ``gp_Ax2(P, N)`` picks +x here).
    """
    r = 0.5
    ref = (np.cos(ref_angle), np.sin(ref_angle), 0.0)
    circle = geo_cu.Circle(geo_su.Axis2Placement3D(location=Point(0, 0, 0), axis=(0, 0, 1), ref_direction=ref), r)
    t = (2 * np.pi - ref_angle, 2 * np.pi - turn - ref_angle)
    end = (r * np.cos(-turn), r * np.sin(-turn), 0.0)
    loop = [
        _line((0, 0, 0), (r, 0, 0)),
        _edge((r, 0, 0), end, circle, same_sense=False, t=t),
        _line(end, (0, 0, 0)),
    ]
    plane = geo_su.Plane(geo_su.Axis2Placement3D(location=Point(0, 0, 0), axis=(0, 0, -1), ref_direction=(1, 0, 0)))
    face = geo_su.AdvancedFace(
        bounds=[geo_su.FaceBound(geo_cu.EdgeLoop(loop), True)], face_surface=plane, same_sense=True
    )
    area, valid = _area_and_validity(backend, face)
    assert valid
    assert area == pytest.approx(r**2 * turn / 2, rel=1e-12)


@pytest.mark.parametrize("turn", [0.5 * np.pi, 1.5 * np.pi])
@pytest.mark.parametrize("ref_angle", [0.0, 1.1])
def test_an_elliptic_sector_whose_arc_runs_backwards(backend, ref_angle, turn):
    """The sector above on an ellipse (semi-axes 0.8, 0.5): area a b turn / 2.

    Both kernels trimmed an open elliptic arc between its points walking the parameter up,
    whichever way the edge ran: measured, the quarter built as three quarters (0.9425 for
    0.3142) on adacpp and the reverse on pythonocc, whose planar builder then also rebuilt a
    three-quarter sector from the shortest arc between its ends (0.3133).
    """
    a, b = 0.8, 0.5
    ref = np.array([np.cos(ref_angle), np.sin(ref_angle), 0.0])
    y = np.cross((0.0, 0.0, 1.0), ref)
    ellipse = geo_cu.Ellipse(
        geo_su.Axis2Placement3D(location=Point(0, 0, 0), axis=(0, 0, 1), ref_direction=tuple(ref)), a, b
    )
    t = (2 * np.pi, 2 * np.pi - turn)
    start, end = (tuple(a * np.cos(s) * ref + b * np.sin(s) * y) for s in t)
    loop = [_line((0, 0, 0), start), _edge(start, end, ellipse, same_sense=False, t=t), _line(end, (0, 0, 0))]
    plane = geo_su.Plane(geo_su.Axis2Placement3D(location=Point(0, 0, 0), axis=(0, 0, -1), ref_direction=(1, 0, 0)))
    face = geo_su.AdvancedFace(
        bounds=[geo_su.FaceBound(geo_cu.EdgeLoop(loop), True)], face_surface=plane, same_sense=True
    )
    area, valid = _area_and_validity(backend, face)
    assert valid
    assert area == pytest.approx(a * b * turn / 2, rel=1e-12)
