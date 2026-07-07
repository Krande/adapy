"""The generic-Shape placement baker (``Shape.solid_geom`` folds a non-identity placement
into the analytic solid's ``position``).

A generic ``Shape`` (as minted by the IFC/native readers) holds its geometry in LOCAL
representation coordinates and its world transform on ``self.placement``. The tessellator and
the STEP/AP242 exporters are placement-agnostic for such shapes — nothing applies the shape's
own placement downstream — so ``solid_geom()`` has to return world-placed geometry, otherwise
a rotated shape renders with its rotation dropped. These tests pin that the bake is applied,
is correct (matches the Placement 4x4 convention), and is idempotent (never mutates / never
compounds on repeat calls).
"""

import numpy as np

from ada import Placement, Point, Shape
from ada.geom import Geometry
from ada.geom.solids import Box


def _box_shape(place: Placement) -> Shape:
    box = Box.from_2points(Point(0, 0, 0), Point(2, 1, 1))
    return Shape("boxshape", geom=Geometry("b", box, None), placement=place)


def test_identity_placement_leaves_geometry_untouched():
    sh = _box_shape(Placement())
    sg = sh.solid_geom()
    # Same object, position unchanged at the local origin.
    assert np.allclose(np.asarray(sg.geometry.position.location), (0, 0, 0))


def test_rotated_placement_is_baked_into_position():
    # 90deg about Z at origin (5, 2, 0): local +X -> world +Y, translation applied.
    place = Placement.from_axis_angle([0, 0, 1], 90, origin=(5, 2, 0))
    sh = _box_shape(place)
    pos = sh.solid_geom().geometry.position

    loc_expected = (place.get_matrix4x4() @ np.array([0.0, 0, 0, 1]))[:3]
    assert np.allclose(np.asarray(pos.location), loc_expected)
    assert np.allclose(np.asarray(pos.ref_direction), (0, 1, 0), atol=1e-6)  # local X -> world Y
    assert np.allclose(np.asarray(pos.axis), (0, 0, 1), atol=1e-6)  # Z preserved under a Z-rotation


def test_bake_is_idempotent_and_non_mutating():
    place = Placement.from_axis_angle([0, 0, 1], 90, origin=(5, 2, 0))
    sh = _box_shape(place)
    loc_expected = (place.get_matrix4x4() @ np.array([0.0, 0, 0, 1]))[:3]

    first = sh.solid_geom().geometry.position.location
    second = sh.solid_geom().geometry.position.location
    assert np.allclose(np.asarray(first), loc_expected)
    assert np.allclose(np.asarray(second), loc_expected)  # not compounded
    # The stored geometry (local) is never mutated by the bake.
    assert np.allclose(np.asarray(sh.geom.geometry.position.location), (0, 0, 0))


def test_baked_geometry_renders_placed_via_stream_and_occ():
    """The baked ``solid_geom()`` tessellates to WORLD coordinates on both the libtess2 stream
    kernel and OCC — the actual bug the baker fixes (a rotated shape rendered unplaced)."""
    import pytest

    place = Placement.from_axis_angle([0, 0, 1], 90, origin=(5, 2, 0))
    sh = _box_shape(place)
    sg = sh.solid_geom()

    # local box (0,0,0)-(2,1,1) rotated 90 about Z at (5,2,0) -> x in [4,5], y in [2,4], z in [0,1]
    def _check_bbox(mn, mx):
        assert 3.9 < mn[0] and mx[0] < 5.1, (mn, mx)
        assert 1.9 < mn[1] and mx[1] < 4.1, (mn, mx)

    # OCC path (solid_occ builds from the baked solid_geom)
    occ = pytest.importorskip("OCC")  # noqa: F841
    from OCC.Core.Bnd import Bnd_Box
    from OCC.Core.BRepBndLib import brepbndlib

    bb = Bnd_Box()
    brepbndlib.Add(sh.solid_occ(), bb)
    xmin, ymin, zmin, xmax, ymax, zmax = bb.Get()
    _check_bbox((xmin, ymin, zmin), (xmax, ymax, zmax))


def test_baked_geometry_renders_placed_via_libtess2():
    import pytest

    pytest.importorskip("adacpp")
    from ada.cad import AdacppBackend

    place = Placement.from_axis_angle([0, 0, 1], 90, origin=(5, 2, 0))
    sh = _box_shape(place)
    sg = sh.solid_geom()

    be = AdacppBackend()
    m = be.tessellate_stream([("b", sg)], pipeline="libtess2", deflection=2.0, angular_deg=20.0)
    pos_attr = getattr(m, "positions", None)
    if pos_attr is None:
        pos_attr = m.position
    pts = np.asarray(pos_attr, dtype=float).reshape(-1, 3)
    mn, mx = pts.min(0), pts.max(0)
    assert 3.9 < mn[0] and mx[0] < 5.1, (mn, mx)
    assert 1.9 < mn[1] and mx[1] < 4.1, (mn, mx)
