"""Closed-revolution AdvancedFace build (adacpp OCC path).

Audit run ed74aca6 had ~50 ``build_advanced_face_{cylindrical,toroidal}: wire build failed``
on STEP files: a full cylinder/torus face's boundary carries a *closed* full-circle edge
(plus a seam) whose vertex must sit where the seam attaches. The adacpp Circle edge record
dropped the circle's ``ref_direction`` (angular origin) and the start vertex, so the closed
edge landed at OCC's default x-axis and the wire wouldn't close. These synthetic neutral-geom
fixtures reproduce that exact mode (seam at 45 deg so a default-axis vertex misses it) and must
now build.
"""

from __future__ import annotations

import math

import pytest

import ada.geom.curves as cu
import ada.geom.surfaces as su
from ada.geom import Geometry
from ada.geom.placement import Axis2Placement3D, Direction, Point


def _adacpp_backend():
    from ada.cad import select_backend

    try:
        return select_backend(prefer="adacpp")
    except ImportError:
        pytest.skip("adacpp backend not installed")


def _oe(start, end, geom):
    ec = cu.EdgeCurve(start=start, end=end, edge_geometry=geom, same_sense=True)
    return cu.OrientedEdge(start=start, end=end, edge_element=ec, orientation=True)


def closed_cylinder_face(r=1.0, h=2.0):
    """Full 360 deg cylinder side: full bottom circle + seam up + full top circle + seam down,
    with the seam at 45 deg so the closed-circle vertices must be anchored to their start point."""
    c = r * math.cos(math.pi / 4)
    pos = Axis2Placement3D(Point(0, 0, 0), Direction(0, 0, 1), Direction(1, 0, 0))
    cyl = su.CylindricalSurface(position=pos, radius=r)
    pb, pt = Point(c, c, 0), Point(c, c, h)
    cb = cu.Circle(position=Axis2Placement3D(Point(0, 0, 0), Direction(0, 0, 1), Direction(1, 0, 0)), radius=r)
    ct = cu.Circle(position=Axis2Placement3D(Point(0, 0, h), Direction(0, 0, 1), Direction(1, 0, 0)), radius=r)
    loop = cu.EdgeLoop(
        edge_list=[
            _oe(pb, pb, cb),
            _oe(pb, pt, cu.Line(pb, Direction(0, 0, 1))),
            _oe(pt, pt, ct),
            _oe(pt, pb, cu.Line(pt, Direction(0, 0, -1))),
        ]
    )
    return su.AdvancedFace(bounds=[su.FaceBound(bound=loop, orientation=True)], face_surface=cyl, same_sense=True)


def closed_torus_face(major=3.0, minor=1.0):
    """Torus tube segment (90 deg major sweep): a full minor circle (closed) at each end joined
    by outer-equator seam arcs — the [full-circle, arc, full-circle, arc] boundary the audit's
    torus faces use."""
    R, r = major, minor
    tor = su.ToroidalSurface(
        position=Axis2Placement3D(Point(0, 0, 0), Direction(0, 0, 1), Direction(1, 0, 0)),
        major_radius=R,
        minor_radius=r,
    )
    s0, s1 = Point(R + r, 0, 0), Point(0, R + r, 0)
    mc0 = cu.Circle(position=Axis2Placement3D(Point(R, 0, 0), Direction(0, 1, 0), Direction(1, 0, 0)), radius=r)
    mc1 = cu.Circle(position=Axis2Placement3D(Point(0, R, 0), Direction(-1, 0, 0), Direction(0, 1, 0)), radius=r)
    mid = Point((R + r) * math.cos(math.pi / 4), (R + r) * math.sin(math.pi / 4), 0)
    loop = cu.EdgeLoop(
        edge_list=[
            _oe(s0, s0, mc0),
            _oe(s0, s1, cu.ArcLine(start=s0, midpoint=mid, end=s1)),
            _oe(s1, s1, mc1),
            _oe(s1, s0, cu.ArcLine(start=s1, midpoint=mid, end=s0)),
        ]
    )
    return su.AdvancedFace(bounds=[su.FaceBound(bound=loop, orientation=True)], face_surface=tor, same_sense=True)


def near_planar_quad(out_of_plane=1e-4):
    """A flat plate whose boundary is only NEAR-planar (one vertex nudged out of the declared
    z=0 plane by more than Precision::Confusion). MakeFace(wire) alone runs FindPlane and fails
    ("build_advanced_face_planar: MakeFace failed", ~27 audit failures); building on the declared
    plane trims it fine."""
    pl = su.Plane(position=Axis2Placement3D(Point(0, 0, 0), Direction(0, 0, 1), Direction(1, 0, 0)))
    p = [Point(0, 0, 0), Point(1, 0, 0), Point(1, 1, out_of_plane), Point(0, 1, 0)]
    loop = cu.EdgeLoop(
        edge_list=[
            _oe(p[i], p[(i + 1) % 4], cu.Line(p[i], Direction(*(p[(i + 1) % 4][k] - p[i][k] for k in range(3)))))
            for i in range(4)
        ]
    )
    return su.AdvancedFace(bounds=[su.FaceBound(bound=loop, orientation=True)], face_surface=pl, same_sense=True)


@pytest.mark.parametrize(
    "name,builder",
    [("cylinder", closed_cylinder_face), ("torus", closed_torus_face), ("near_planar", near_planar_quad)],
)
def test_advanced_face_builds(name, builder):
    # Pre-fix: cylinder/torus raised "wire build failed"; near_planar raised "MakeFace failed".
    backend = _adacpp_backend()
    shape = backend.build(Geometry(name, builder()))
    assert shape is not None
