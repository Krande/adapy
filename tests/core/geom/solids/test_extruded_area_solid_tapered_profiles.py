"""Lofted solids between two profiles: circular sections, oblique sweeps, and
the sections that collapse.

An ``ExtrudedAreaSolidTapered`` is the general frustum -- the start profile and
the end profile need not be the same size, and (IFC's ``ExtrudedDirection``,
inherited from ``IfcExtrudedAreaSolid``) need not be stacked along the profile
normal either. Volumes are checked against the prismatoid rule,

    V = h / 6 * (A_bottom + 4 * A_middle + A_top)

which is exact for any solid whose cross-sectional area is at most cubic in the
sweep parameter -- every case here.
"""

from __future__ import annotations

import math

import pytest

from ada.cad import active_backend
from ada.geom import Geometry
from ada.geom import solids as geo_so
from ada.geom import surfaces as geo_su
from ada.geom.direction import Direction
from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point

AREA = geo_su.ProfileType.AREA
PLACE = Axis2Placement3D(Point(0, 0, 0), Direction(0, 0, 1), Direction(1, 0, 0))


def circle(radius: float) -> geo_su.CircleProfileDef:
    return geo_su.CircleProfileDef(AREA, radius)


def rectangle(x_dim: float, y_dim: float) -> geo_su.RectangleProfileDef:
    return geo_su.RectangleProfileDef(AREA, x_dim, y_dim)


def _builds_oblique() -> bool:
    """Can the ACTIVE backend displace the end section off-axis?

    An older ada-cpp translates the end profile along +Z and takes no direction,
    so it builds right frustums only and says so with NotImplementedError. The
    cases below that need an oblique one skip rather than fail: "this build
    cannot do it yet" and "this build gets it wrong" are different findings, and
    only the second is a bug -- which is the whole reason that backend refuses
    instead of quietly returning a right frustum.
    """
    probe = geo_so.ExtrudedAreaSolidTapered(circle(1.0), PLACE, 1.0, Direction(1, 0, 1), circle(1.0))
    try:
        active_backend().build(Geometry(0, probe, None))
    except NotImplementedError:
        return False
    except Exception:
        return True
    return True


needs_oblique = pytest.mark.skipif(not _builds_oblique(), reason="this CAD backend builds right frustums only")


def build(solid):
    """Through the CAD backend, not a kernel import, so the assertions below
    hold whichever kernel is active."""
    return active_backend().build(Geometry(0, solid, None))


def volume_of(solid) -> float:
    return active_backend().volume(build(solid))


def prismatoid(h: float, a_bottom: float, a_middle: float, a_top: float) -> float:
    return h / 6.0 * (a_bottom + 4.0 * a_middle + a_top)


# ---------------------------------------------------------------------------
# Circular sections
# ---------------------------------------------------------------------------


def test_truncated_cone_between_two_circles():
    """The frustum. A circular profile had no buildable outline before, so this
    whole family raised rather than producing a solid."""
    r0, r1, h = 1.0, 0.5, 2.0
    solid = geo_so.ExtrudedAreaSolidTapered(circle(r0), PLACE, h, Direction(0, 0, 1), circle(r1))
    expected = math.pi * h / 3.0 * (r0**2 + r0 * r1 + r1**2)

    assert volume_of(solid) == pytest.approx(expected, rel=1e-3)


def test_equal_circles_give_a_cylinder():
    r, h = 0.75, 3.0
    solid = geo_so.ExtrudedAreaSolidTapered(circle(r), PLACE, h, Direction(0, 0, 1), circle(r))

    assert volume_of(solid) == pytest.approx(math.pi * r * r * h, rel=1e-3)


def test_a_circular_profile_stays_analytic():
    """One lateral face, not one per facet.

    The outline is a Circle rather than a polygon, so the swept side is a
    single (trimmed) conical surface and the solid has three faces: the side
    and the two planar caps. An outline approximated as an n-gon would give n
    lateral faces and freeze the facet count at profile-construction time.

    The face count is the structural half of that claim; the volume tests above
    are the numeric half -- a 12-gon approximation of these circles would be
    about 4% light and miss their `rel=1e-3`.
    """
    backend = active_backend()
    solid = geo_so.ExtrudedAreaSolidTapered(circle(1.0), PLACE, 2.0, Direction(0, 0, 1), circle(0.5))

    assert len(backend.faces(build(solid))) == 3


# ---------------------------------------------------------------------------
# Rectangular sections
# ---------------------------------------------------------------------------


def test_rectangular_frustum():
    (x0, y0), (x1, y1), h = (4.0, 3.0), (2.0, 1.0), 5.0
    solid = geo_so.ExtrudedAreaSolidTapered(rectangle(x0, y0), PLACE, h, Direction(0, 0, 1), rectangle(x1, y1))
    expected = prismatoid(h, x0 * y0, ((x0 + x1) / 2) * ((y0 + y1) / 2), x1 * y1)

    assert volume_of(solid) == pytest.approx(expected, rel=1e-6)


# ---------------------------------------------------------------------------
# An oblique sweep
# ---------------------------------------------------------------------------


@needs_oblique
def test_an_oblique_sweep_has_the_volume_cavalieri_gives():
    """An oblique cylinder: the sections stay parallel and the same size, so the
    volume is the section area times the PERPENDICULAR distance between the two
    planes -- `depth` projected onto the profile normal, not `depth` itself.

    Sweeping at 45 degrees therefore gives 1/sqrt(2) of the upright solid, and
    getting the projection wrong is visible as exactly that factor.
    """
    r, depth = 1.0, 2.0
    oblique = Direction(1, 0, 1)
    unit = [c / math.sqrt(2) for c in (1, 0, 1)]
    perpendicular = depth * unit[2]  # dot with the profile normal, +Z

    solid = geo_so.ExtrudedAreaSolidTapered(circle(r), PLACE, depth, oblique, circle(r))

    assert volume_of(solid) == pytest.approx(math.pi * r * r * perpendicular, rel=1e-3)


@needs_oblique
def test_the_end_section_lands_where_the_direction_points():
    """The displacement itself, read off the bounding box.

    A 45-degree sweep, not a fully lateral one: with the direction in the
    profile's own plane the two sections would be coplanar and the solid would
    have no thickness at all.
    """
    r, depth = 0.5, 2.0
    reach = depth / math.sqrt(2)
    solid = geo_so.ExtrudedAreaSolidTapered(circle(r), PLACE, depth, Direction(1, 0, 1), circle(r))
    xmin, _ymin, zmin, xmax, _ymax, zmax = active_backend().bbox(build(solid), optimal=True)

    # The end centre sits at (reach, 0, reach); the section adds its own radius
    # in X and nothing in Z, since the sections are normal to Z.
    assert (xmax - xmin) == pytest.approx(reach + 2 * r, abs=1e-6)
    assert (zmax - zmin) == pytest.approx(reach, abs=1e-6)


def test_an_axial_direction_is_the_previous_behaviour():
    """`extruded_direction` used to be ignored, so the one direction that must
    not change is the one everything already passes."""
    r0, r1, h = 1.0, 0.5, 2.0
    solid = geo_so.ExtrudedAreaSolidTapered(circle(r0), PLACE, h, Direction(0, 0, 1), circle(r1))
    expected = math.pi * h / 3.0 * (r0**2 + r0 * r1 + r1**2)

    assert volume_of(solid) == pytest.approx(expected, rel=1e-3)


def test_a_non_unit_direction_does_not_scale_the_solid():
    """`depth` is the length; the direction only says which way."""
    r = 0.5
    one = geo_so.ExtrudedAreaSolidTapered(circle(r), PLACE, 2.0, Direction(0, 0, 1), circle(r))
    ten = geo_so.ExtrudedAreaSolidTapered(circle(r), PLACE, 2.0, Direction(0, 0, 10), circle(r))

    assert volume_of(ten) == pytest.approx(volume_of(one), rel=1e-9)


def test_a_zero_direction_is_refused():
    solid = geo_so.ExtrudedAreaSolidTapered(circle(1.0), PLACE, 2.0, Direction(0, 0, 0), circle(0.5))

    with pytest.raises(ValueError, match="zero-length"):
        build(solid)


# ---------------------------------------------------------------------------
# Sections that collapse
# ---------------------------------------------------------------------------


def test_a_circle_collapsing_to_a_point_names_the_cone():
    solid = geo_so.ExtrudedAreaSolidTapered(circle(1.0), PLACE, 2.0, Direction(0, 0, 1), circle(0.0))

    with pytest.raises(ValueError, match="cone"):
        build(solid)


def test_a_rectangle_collapsing_to_a_point_names_the_pyramid():
    solid = geo_so.ExtrudedAreaSolidTapered(rectangle(2.0, 2.0), PLACE, 2.0, Direction(0, 0, 1), rectangle(0.0, 0.0))

    with pytest.raises(ValueError, match="pyramid"):
        build(solid)


def test_a_rectangle_collapsing_to_an_edge_names_the_wedge():
    """The case the lofter cannot bound at all -- it reports failure rather
    than raising, so without this check the caller gets an invalid shape."""
    solid = geo_so.ExtrudedAreaSolidTapered(rectangle(2.0, 2.0), PLACE, 2.0, Direction(0, 0, 1), rectangle(0.0, 2.0))

    with pytest.raises(ValueError, match="wedge"):
        build(solid)
