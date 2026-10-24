"""A revolve whose profile carries an elliptical arc is an exact solid, and is written as one.

The case is a dished end: a quarter ellipse of semi-axes R (radial) and H (axial) closed by two
lines and revolved a full turn -- a half ellipsoid of volume 2/3 pi R^2 H.
"""

from __future__ import annotations

import math

import pytest

import ada
from ada.geom import Geometry
from ada.geom import curves as geo_cu
from ada.geom import solids as geo_so
from ada.geom import surfaces as geo_su
from ada.geom.placement import Axis1Placement, Axis2Placement3D, Direction
from ada.geom.points import Point

R, H = 2.0, 1.0
HALF_ELLIPSOID = 2.0 / 3.0 * math.pi * R * R * H


def _dish(name: str = "dish") -> ada.Shape:
    quarter = geo_cu.TrimmedCurve(
        basis_curve=geo_cu.Ellipse(Axis2Placement3D(Point(0, 0, 0), Direction(0, 0, 1), Direction(1, 0, 0)), R, H),
        trim1=Point(R, 0, 0),
        trim2=Point(0, H, 0),
        sense_agreement=True,
        master_representation="CARTESIAN",
    )
    profile = geo_cu.CompositeCurve(
        [
            geo_cu.CompositeCurveSegment(geo_cu.PolyLine([Point(0, 0, 0), Point(R, 0, 0)]), True),
            geo_cu.CompositeCurveSegment(quarter, True),
            geo_cu.CompositeCurveSegment(geo_cu.PolyLine([Point(0, H, 0), Point(0, 0, 0)]), True),
        ]
    )
    solid = geo_so.RevolvedAreaSolid(
        swept_area=geo_su.ArbitraryProfileDef(geo_su.ProfileType.AREA, profile),
        # The profile's local Y (the ellipse's semi-axis H) runs along world Z, its X radially.
        position=Axis2Placement3D(Point(0, 0, 0), Direction(0, -1, 0), Direction(1, 0, 0)),
        axis=Axis1Placement(Point(0, 0, 0), Direction(0, 0, 1)),
        angle=360.0,
    )
    return ada.Shape(name, geom=Geometry(name, solid, None))


def test_an_elliptical_arc_revolves_to_the_exact_half_ellipsoid(backend):
    shape = backend.build(_dish().solid_geom())
    assert backend.volume(shape) == pytest.approx(HALF_ELLIPSOID, rel=1e-9)
    x0, y0, z0, x1, y1, z1 = backend.bbox(shape)
    assert (x1 - x0, y1 - y0, z1 - z0) == pytest.approx((2 * R, 2 * R, H), abs=1e-6)


def test_the_half_ellipsoid_takes_a_bore(backend):
    r = 0.5
    dish = _dish()
    dish.add_boolean(ada.PrimCyl("bore", (0, 0, -5), (0, 0, 5), r))
    # The bore takes what lies within r of the axis: 2 pi int_0^r rho H sqrt(1 - rho^2/R^2) d rho.
    bore = 2 * math.pi * (H * R * R / 3) * (1 - (1 - (r / R) ** 2) ** 1.5)
    assert backend.volume(backend.build(dish.solid_geom())) == pytest.approx(HALF_ELLIPSOID - bore, rel=1e-6)


def test_the_half_ellipsoid_is_written_with_its_ellipse(tmp_path):
    a = ada.Assembly("a") / [ada.Part("p") / [_dish()]]
    a.to_stp(tmp_path / "dish.stp")
    a.to_ifc(tmp_path / "dish.ifc")
    step = (tmp_path / "dish.stp").read_text(errors="replace").upper()
    ifc = (tmp_path / "dish.ifc").read_text(errors="replace").upper()
    assert "ELLIPSE(" in step
    assert "IFCREVOLVEDAREASOLID" in ifc and "IFCELLIPSE" in ifc
    assert "IFCTRIANGULATEDFACESET" not in ifc


def test_a_tall_quarter_ellipse_revolves_too(backend):
    """Semi-axis along the profile's X shorter than along its Y: the kernel's ellipse wants its
    major radius first, and the profile's quarter ellipse is the same curve either way."""
    r, h = 0.5, 1.5
    quarter = geo_cu.TrimmedCurve(
        basis_curve=geo_cu.Ellipse(Axis2Placement3D(Point(0, 0, 0), Direction(0, 0, 1), Direction(1, 0, 0)), r, h),
        trim1=Point(r, 0, 0),
        trim2=Point(0, h, 0),
        sense_agreement=True,
        master_representation="CARTESIAN",
    )
    profile = geo_cu.CompositeCurve(
        [
            geo_cu.CompositeCurveSegment(geo_cu.PolyLine([Point(0, 0, 0), Point(r, 0, 0)]), True),
            geo_cu.CompositeCurveSegment(quarter, True),
            geo_cu.CompositeCurveSegment(geo_cu.PolyLine([Point(0, h, 0), Point(0, 0, 0)]), True),
        ]
    )
    solid = geo_so.RevolvedAreaSolid(
        swept_area=geo_su.ArbitraryProfileDef(geo_su.ProfileType.AREA, profile),
        position=Axis2Placement3D(Point(0, 0, 0), Direction(0, -1, 0), Direction(1, 0, 0)),
        axis=Axis1Placement(Point(0, 0, 0), Direction(0, 0, 1)),
        angle=360.0,
    )
    shape = backend.build(ada.Shape("tall", geom=Geometry("tall", solid, None)).solid_geom())
    assert backend.volume(shape) == pytest.approx(2.0 / 3.0 * math.pi * r * r * h, rel=1e-9)
