"""An arc whose edge runs against its circle is the short arc between its trims, on either kernel.

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
