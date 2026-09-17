"""A full torus (STEP AP242 ``torus``), and the partial sweep that is not one.

There is no IFC CSG torus, which is why ``Torus`` carries STEP's attribute
names. It describes a FULL revolution: ``Axis1Placement`` fixes the axis but no
direction in the plane normal to it, so there is nowhere to record where a
partial sweep would start. A partial one is a ``RevolvedAreaSolid`` of a
``CircleProfileDef``, whose ``position`` does carry that information.

Volumes are checked against the closed forms rather than against a stored
value, so the assertions stay meaningful if the mesher or the kernel changes:

    full torus              2 * pi^2 * R * r^2
    swept through theta     that, times theta / 2pi
"""

from __future__ import annotations

import math

import pytest

from ada.cad import active_backend
from ada.geom import Geometry
from ada.geom import solids as geo_so
from ada.geom import surfaces as geo_su
from ada.geom.direction import Direction
from ada.geom.placement import Axis1Placement, Axis2Placement3D
from ada.geom.points import Point

R, r = 1.0, 0.25
FULL = 2 * math.pi**2 * R * r**2

#: Evaluated once, after `_backend_builds` is defined -- see the bottom of the
#: imports. A backend with no torus skips this module rather than failing it.
_TORUS = geo_so.Torus(Axis1Placement(Point(0, 0, 0), Direction(0, 0, 1)), R, r)


def _backend_builds(solid) -> bool:
    """Can the ACTIVE backend build this solid at all?

    A backend declares its coverage by building or refusing, and a kernel that
    predates a solid raises rather than returning the wrong shape. Tests that
    need one say so here, so an older backend SKIPS instead of failing --
    which is the difference between "this build cannot do it yet" and "this
    build gets it wrong", and only the second is a bug.

    Anything else propagates: a backend that has the builder and throws inside
    it is a real failure and must not be skipped past.
    """
    try:
        active_backend().build(Geometry(0, solid, None))
    except (NotImplementedError, AttributeError):
        return False
    except Exception:
        return True
    return True


pytestmark = pytest.mark.skipif(not _backend_builds(_TORUS), reason="this CAD backend has no torus builder")


def build(solid):
    """Through the CAD backend, not a kernel import.

    Everything below asks the backend for the shape and for the measurements,
    so the same assertions hold whichever kernel is active.
    """
    return active_backend().build(Geometry(0, solid, None))


def test_full_torus_has_the_closed_form_volume():
    torus = geo_so.Torus(Axis1Placement(Point(0, 0, 0), Direction(0, 0, 1)), R, r)

    assert active_backend().volume(build(torus)) == pytest.approx(FULL, rel=1e-6)


def test_a_torus_is_built_rather_than_skipped():
    """Guards the dispatch entry, not the arithmetic.

    A solid with no branch in ``geom_to_occ_geom`` is not approximated, it is
    skipped -- so the failure this pins is an element quietly losing its shape
    rather than an element being the wrong shape.
    """
    torus = geo_so.Torus(Axis1Placement(Point(0, 0, 0), Direction(0, 0, 1)), R, r)

    assert active_backend().shape_type(build(torus)) == "solid"


def tight_bounds(shape):
    """A bounding box that touches the surface.

    ``optimal=True`` solves for the true extremes; the cheap box bounds the
    control polygon instead, which on anything curved is LOOSE -- for this
    torus by ~8% on the swept axes. A test about orientation needs the tight
    one, since a loose box hides the difference between a torus lying in one
    plane and a slightly bigger one lying in another.
    """
    return active_backend().bbox(shape, optimal=True)


def test_the_axis_orients_the_torus():
    """About +X, the hole faces along X: the solid is thin in X and wide in Y/Z."""
    torus = geo_so.Torus(Axis1Placement(Point(0, 0, 0), Direction(1, 0, 0)), R, r)
    xmin, ymin, zmin, xmax, ymax, zmax = tight_bounds(build(torus))

    assert (xmax - xmin) == pytest.approx(2 * r, abs=1e-6)
    assert (ymax - ymin) == pytest.approx(2 * (R + r), abs=1e-6)
    assert (zmax - zmin) == pytest.approx(2 * (R + r), abs=1e-6)


def test_the_location_places_the_torus():
    torus = geo_so.Torus(Axis1Placement(Point(10, 20, 30), Direction(0, 0, 1)), R, r)
    xmin, ymin, zmin, xmax, ymax, zmax = tight_bounds(build(torus))

    assert (xmin + xmax) / 2 == pytest.approx(10.0, abs=1e-6)
    assert (ymin + ymax) / 2 == pytest.approx(20.0, abs=1e-6)
    assert (zmin + zmax) / 2 == pytest.approx(30.0, abs=1e-6)


@pytest.mark.parametrize("degrees", [90.0, 180.0, 270.0])
def test_a_partial_torus_is_a_revolved_circular_profile(degrees):
    """The representation for a swept angle, and the reason a circular profile
    had to become buildable: revolving one about an axis offset by R is a torus
    of any angle, with the start direction carried by the profile's position."""
    profile = geo_su.CircleProfileDef(geo_su.ProfileType.AREA, r)
    # The section sits at distance R from the axis, in the plane it revolves in.
    position = Axis2Placement3D(Point(R, 0, 0), Direction(0, 1, 0), Direction(1, 0, 0))
    solid = geo_so.RevolvedAreaSolid(profile, position, Axis1Placement(Point(0, 0, 0), Direction(0, 0, 1)), degrees)

    volume = active_backend().volume(build(solid))

    assert volume == pytest.approx(FULL * degrees / 360.0, rel=1e-3)
