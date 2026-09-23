"""``IndexedPolyCurve.to_points2d`` -- the outline's corners, once each.

The reader turns this into a ``Plate``'s outline, so a corner named twice becomes a polygon with
zero-length segments: it still renders (the duplicates are collinear) but every question about the
OUTLINE -- which edges are there, what runs along them -- is then asked of a degenerate polygon.
That is how a model round-tripped through IFC lost a third of its clash-check joints.
"""

import numpy as np

from ada.geom import curves as geo_cu


def _square() -> geo_cu.IndexedPolyCurve:
    corners = [(5.0, 0.0), (0.0, 0.0), (0.0, 5.0), (5.0, 5.0)]
    segments = [geo_cu.Edge(corners[i], corners[(i + 1) % len(corners)]) for i in range(len(corners))]
    return geo_cu.IndexedPolyCurve(segments=segments)


def test_a_square_has_four_corners_each_named_once():
    pts = [tuple(float(v) for v in p) for p in _square().to_points2d()]
    assert pts == [(5.0, 0.0), (0.0, 0.0), (0.0, 5.0), (5.0, 5.0)]


def test_no_two_consecutive_points_coincide():
    pts = np.asarray([[float(v) for v in p] for p in _square().to_points2d()], dtype=float)
    rolled = np.roll(pts, -1, axis=0)
    assert np.all(np.linalg.norm(rolled - pts, axis=1) > 1e-12)


def test_an_arc_contributes_its_fillet_corner_and_radius():
    # A unit square with the (1, 0) corner rounded at r = 0.2: the arc's own endpoints are the
    # tangent points, and the corner it stands for is the one the two lines would have met at.
    r = 0.2
    mid = (1.0 - r + r * np.cos(np.pi / 4), r - r * np.sin(np.pi / 4))
    segments = [
        geo_cu.Edge((0.0, 0.0), (1.0 - r, 0.0)),
        geo_cu.ArcLine((1.0 - r, 0.0), mid, (1.0, r)),
        geo_cu.Edge((1.0, r), (1.0, 1.0)),
        geo_cu.Edge((1.0, 1.0), (0.0, 1.0)),
        geo_cu.Edge((0.0, 1.0), (0.0, 0.0)),
    ]
    pts = [tuple(float(v) for v in p) for p in geo_cu.IndexedPolyCurve(segments=segments).to_points2d()]

    assert len(pts) == 4, "one point per corner: the arc replaces the corner it rounds"
    assert pts[0] == (0.0, 0.0)
    # The rounded corner, carrying its radius -- and the line that follows the arc does NOT also
    # emit its (tangent) start point.
    assert len(pts[1]) == 3
    assert np.allclose(pts[1][:2], (1.0, 0.0), atol=1e-9)
    assert np.isclose(pts[1][2], r, atol=1e-9)
    assert pts[2] == (1.0, 1.0)
    assert pts[3] == (0.0, 1.0)


def test_an_empty_curve_is_an_empty_outline():
    assert geo_cu.IndexedPolyCurve(segments=[]).to_points2d() == []
