"""A loop that runs a whole closed curve backwards is built backwards, on either kernel.

A closed curve built whole has no endpoints to say which way its loop runs it, so the edge's
sense has to: a round hole is the outline's curve run the other way, ``same_sense`` false.
Built forward regardless, the hole winds like the outline and the face measures the outline
plus the hole. The pythonocc builder honours the sense (``make_edge_from_edge`` and
``make_wire_from_edge_loop``); the adacpp encoder did not -- measured on ada-cpp 0.31: a unit
square less a round hole of radius 0.2 built invalid, 1.1257 m2 instead of 0.8743
(Krande/adapy#435).
"""

from __future__ import annotations

import numpy as np
import pytest

from ada.geom import Geometry
from ada.geom import curves as geo_cu
from ada.geom import surfaces as geo_su
from ada.geom.points import Point

R = 0.2
CENTRE = (0.5, 0.5, 0.0)


def _line(p1, p2) -> geo_cu.OrientedEdge:
    a, b = Point(*p1), Point(*p2)
    line = geo_cu.Line(a, np.subtract(p2, p1))
    ec = geo_cu.EdgeCurve(start=a, end=b, edge_geometry=line, same_sense=True)
    return geo_cu.OrientedEdge(start=a, end=b, edge_element=ec, orientation=True)


def _square() -> geo_su.FaceBound:
    c = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
    return geo_su.FaceBound(geo_cu.EdgeLoop([_line(c[i], c[(i + 1) % 4]) for i in range(4)]), True)


def _position():
    return geo_su.Axis2Placement3D(location=Point(*CENTRE), axis=(0, 0, 1), ref_direction=(1, 0, 0))


def _circle():
    return geo_cu.Circle(_position(), R)


def _ellipse():
    return geo_cu.Ellipse(_position(), R, R / 2)


def _nurbs_circle():
    """The circle as the rational B-spline GeniE writes for a round hole: degree 2, 9 poles."""
    c = np.asarray(CENTRE)
    corners = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1), (1, 0)]
    poles = [Point(*(c + R * np.array([x, y, 0.0]))) for x, y in corners]
    w = np.sqrt(0.5)
    return geo_cu.RationalBSplineCurveWithKnots(
        degree=2,
        control_points_list=poles,
        curve_form=geo_cu.BSplineCurveFormEnum.CIRCULAR_ARC,
        closed_curve=True,
        self_intersect=False,
        knot_multiplicities=[3, 2, 2, 2, 3],
        knots=[0.0, np.pi / 2, np.pi, 3 * np.pi / 2, 2 * np.pi],
        knot_spec=geo_cu.KnotType.UNSPECIFIED,
        weights_data=[1.0, w, 1.0, w, 1.0, w, 1.0, w, 1.0],
    )


CURVES = {
    "circle": (_circle, np.pi * R**2),
    "ellipse": (_ellipse, np.pi * R * R / 2),
    "nurbs": (_nurbs_circle, np.pi * R**2),
}


def _loop(curve, same_sense: bool) -> geo_su.FaceBound:
    p = Point(CENTRE[0] + R, CENTRE[1], 0.0)
    ts = (0.0, 2 * np.pi) if same_sense else (2 * np.pi, 0.0)
    ec = geo_cu.EdgeCurve(start=p, end=p, edge_geometry=curve, same_sense=same_sense)
    oe = geo_cu.OrientedEdge(start=p, end=p, edge_element=ec, orientation=True, t_start=ts[0], t_end=ts[1])
    return geo_su.FaceBound(geo_cu.EdgeLoop([oe]), True)


@pytest.mark.parametrize("kind", sorted(CURVES))
def test_a_hole_run_backwards_is_cut_from_the_face(backend, kind):
    """Valid, and its area is the square's less the hole's.

    Measured on both kernels: wound like the outline, the face is invalid (BRepCheck) and measures
    1 + the hole. The NURBS hole's area is not compared exactly: both kernels' default
    integration puts that plate at 0.874669 against the exact 0.874336, so there the test asks
    only that the hole was taken away.
    """
    make, hole_area = CURVES[kind]
    plane = geo_su.Plane(
        position=geo_su.Axis2Placement3D(location=Point(0, 0, 0), axis=(0, 0, 1), ref_direction=(1, 0, 0))
    )
    face = geo_su.AdvancedFace(bounds=[_square(), _loop(make(), same_sense=False)], face_surface=plane, same_sense=True)
    shape = backend.build(Geometry(1, face, None))
    assert backend.is_valid(shape)
    area = backend.area(shape)
    if kind == "nurbs":
        assert area < 1.0
    else:
        assert area == pytest.approx(1.0 - hole_area, rel=1e-12)


@pytest.mark.adacpp
def test_a_bspline_run_backwards_is_encoded_as_the_same_points_in_reverse():
    """The reversed record is the same curve run the other way -- knots reflected, not just poles.

    Knots, multiplicities and weights none of them symmetric, so leaving any one unreversed
    encodes a different curve (the circle's are all palindromes and cannot tell). Read straight off the record: no kernel integration in it.
    """
    from ada.cad import AdacppBackend

    curve = _nurbs_circle()
    curve.knots = [0.0, 1.0, 1.5, 2.5, 3.5, 4.0]
    curve.knot_multiplicities = [3, 2, 1, 2, 1, 3]
    curve.weights_data = [1.0, 0.7, 1.0, 0.8, 1.0, 0.6, 1.0, 0.9, 1.0]
    rec = AdacppBackend()._encode_oriented_edge(_loop(curve, same_sense=False).bound.edge_list[0])
    deg, n = int(rec[1]), int(rec[12])
    poles = [Point(*rec[13 + 3 * i : 16 + 3 * i]) for i in range(n)]
    k0 = 13 + 3 * n
    nk = int(rec[k0])
    knots, mults = rec[k0 + 1 : k0 + 1 + nk], [int(m) for m in rec[k0 + 1 + nk : k0 + 1 + 2 * nk]]
    weights = rec[k0 + 1 + 2 * nk :]
    encoded = geo_cu.RationalBSplineCurveWithKnots(
        degree=deg,
        control_points_list=poles,
        curve_form=geo_cu.BSplineCurveFormEnum.UNSPECIFIED,
        closed_curve=True,
        self_intersect=False,
        knot_multiplicities=mults,
        knots=knots,
        knot_spec=geo_cu.KnotType.UNSPECIFIED,
        weights_data=weights,
    )
    dev = np.abs(np.subtract(encoded.sample(41), curve.sample(41)[::-1])).max()
    assert dev <= 1e-15  # measured 3.3e-16: rounding, the curve the same


@pytest.mark.parametrize("kind", sorted(CURVES))
def test_a_closed_curve_run_forwards_is_left_alone(backend, kind):
    """The disc inside the hole: the curve run with its sense, facing the way the plane does.

    A single loop builds a valid face of the same area either way round; what shows its
    direction is the normal adacpp gives the face, which it infers from the loop (measured: run
    backwards, the disc's plane comes out on -z). pythonocc keeps the plane it is given.
    """
    make, disc_area = CURVES[kind]
    plane = geo_su.Plane(
        position=geo_su.Axis2Placement3D(location=Point(0, 0, 0), axis=(0, 0, 1), ref_direction=(1, 0, 0))
    )
    face = geo_su.AdvancedFace(bounds=[_loop(make(), same_sense=True)], face_surface=plane, same_sense=True)
    shape = backend.build(Geometry(1, face, None))
    assert backend.is_valid(shape)
    (built,) = backend.faces(shape)
    _origin, normal = backend.face_plane(built)
    assert np.allclose(normal, (0, 0, 1), rtol=0, atol=1e-15)
    if kind != "nurbs":
        assert backend.area(shape) == pytest.approx(disc_area, rel=1e-12)
