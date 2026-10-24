import numpy as np
import pytest

import ada
import ada.geom.curves as geo_cu
from ada.api.curves import CurvePoly2d
from ada.core.curve_utils import segments3d_from_points3d


def test_line_segment2d():
    line2d = ada.LineSegment((0, 0), (1, 1))
    curve_geom = line2d.curve_geom()
    assert isinstance(curve_geom, geo_cu.Edge)
    assert line2d.p1.is_equal(curve_geom.start)
    assert line2d.p2.is_equal(curve_geom.end)


def test_line_segment3d():
    line3d = ada.LineSegment((0, 0, 0), (1, 1, 1))
    curve_geom = line3d.curve_geom()
    assert isinstance(curve_geom, geo_cu.Edge)
    assert line3d.p1.is_equal(curve_geom.start)
    assert line3d.p2.is_equal(curve_geom.end)


def test_arc_line_2d_from_midpoint():
    arc = ada.ArcSegment((0, 0), (1, 1), (0.5, 1.2))
    curve_geom = arc.curve_geom()
    assert isinstance(curve_geom, geo_cu.ArcLine)
    assert arc.p1.is_equal(curve_geom.start)
    assert arc.p2.is_equal(curve_geom.end)
    assert arc.midpoint.is_equal(curve_geom.midpoint)


def test_make_3d_segments():
    points = [ada.Point(1, 1, 3) + x for x in [(0, 0.5, 0), (1, 0.5, 0), (1.2, 0.7, 0.2), (1.5, 0.7, 0.2)]]
    segments = segments3d_from_points3d(points, radius=0.3979375)

    # This radius will make the two middle ArcSegments connected
    assert len(segments) == 4

    # This radius will make the two middle ArcSegments disconnected by a small LineSegment
    segments2 = segments3d_from_points3d(points, radius=0.116625)
    assert len(segments2) == 5


def test_make_3d_segments_short_leg_between_bends():
    # A short leg (0.15 m) between two 90-degree bends with a fixed radius (0.3 m)
    # larger than the leg: each corner's arc trim would otherwise overrun the leg,
    # eating the straight between the bends and yielding offset/disconnected
    # segments (or a degenerate arc with midpoint=None that crashes). The scalar
    # radius must be clamped per-corner so the run stays continuous.
    points = [ada.Point(*p) for p in [(0, 0, 0), (1, 0, 0), (1, 0.15, 0), (2, 0.15, 0)]]
    segments = segments3d_from_points3d(points, radius=0.3)

    # Every segment end must coincide with the next segment's start (no gaps).
    for a, b in zip(segments[:-1], segments[1:]):
        assert a.p2.is_equal(b.p1), f"discontinuity: {a.p2} != {b.p1}"

    # An even shorter leg (0.05 m) used to crash on a None arc midpoint â€” must not.
    tight = [ada.Point(*p) for p in [(0, 0, 0), (1, 0, 0), (1, 0.05, 0), (2, 0.05, 0)]]
    tight_segments = segments3d_from_points3d(tight, radius=0.3)
    for a, b in zip(tight_segments[:-1], tight_segments[1:]):
        assert a.p2.is_equal(b.p1)


def test_a_plain_point_between_two_filleted_corners_does_not_move_the_second_bend():
    # A U-shaped path: a leg down, a bend, the run across, a bend, a leg up. A point on the run between
    # the two bends (no radius of its own) must leave the path as it was without it: the corner loop
    # used to build the second bend from the leg before that point, a whole leg early, and a sweep along
    # the path came out 11 % light.
    r = 0.074
    plain = [(0, 0.163, 0), (0, 0, 0, r), (-0.288, 0, 0, r), (-0.288, 0.163, 0)]
    with_mid = [(0, 0.163, 0), (0, 0, 0, r), (-0.144, 0, 0), (-0.288, 0, 0, r), (-0.288, 0.163, 0)]

    def run(points):
        pts = [ada.Point(*p[:3]) for p in points]
        radii = {i: p[3] for i, p in enumerate(points) if len(p) == 4}
        return segments3d_from_points3d(pts, radius_dict=radii)

    def shape(segments):
        return [(type(s).__name__, round(s.length, 5)) for s in segments]

    # legs 0.163 - 0.074, the run 0.288 - 2 x 0.074, 0.163 - 0.074; the arcs' chords are r x sqrt(2)
    expected = [
        ("LineSegment", 0.089),
        ("ArcSegment", 0.10465),
        ("LineSegment", 0.14),
        ("ArcSegment", 0.10465),
        ("LineSegment", 0.089),
    ]
    assert shape(run(plain)) == expected
    assert shape(run(with_mid)) == expected


def test_a_plain_point_a_fraction_of_a_millimetre_off_the_run_is_straight():
    # The same U as above, with the point between the bends 0.12 mm off the line of its neighbours (a
    # turn of 0.05 degrees): a hand-placed straight run, not a corner.
    r = 0.074
    expected = [
        ("LineSegment", 0.089),
        ("ArcSegment", 0.10465),
        ("LineSegment", 0.14),
        ("ArcSegment", 0.10465),
        ("LineSegment", 0.089),
    ]
    pts = [ada.Point(*p) for p in [(0, 0.163, 0), (0, 0, 0), (-0.144, 0.00012, 0), (-0.288, 0, 0), (-0.288, 0.163, 0)]]
    segments = segments3d_from_points3d(pts, radius_dict={1: r, 3: r})
    assert [(type(s).__name__, round(s.length, 5)) for s in segments] == expected


def test_a_profile_vertex_written_twice_is_one_corner():
    # A repeated vertex, the second carrying a fillet, left a zero-length edge the corner builder could not
    # normalise.
    plain = CurvePoly2d([(0, 0), (2, 0), (2, 1), (0, 1)])
    repeated = CurvePoly2d([(0, 0), (2, 0), (2, 1), (2, 1, 0.2), (0, 1)])
    assert len(plain.segments) == 4
    assert len(repeated.segments) == 5  # four sides and the fillet arc at (2, 1)


def test_a_profile_thinner_than_the_point_tolerance_raises_instead_of_dropping_its_ends():
    strip = [(0, 0), (1, 0), (1, 0.0005), (0, 0.0005)]  # 0.5 mm wide, 1 m long
    with pytest.raises(ValueError, match="tol"):
        _ = CurvePoly2d(strip).segments
    assert len(CurvePoly2d(strip, tol=1e-5).segments) == 4


def _outline_area(segments) -> float:
    """Area enclosed by a closed outline of line and arc segments: the shoelace area of the segment end points,
    plus the circular-segment area of every arc (the fillets here bulge outwards from their chord)."""
    import math

    pts = [np.asarray(s.p1, dtype=float)[:2] for s in segments]
    shoelace = 0.5 * sum(a[0] * b[1] - b[0] * a[1] for a, b in zip(pts, pts[1:] + pts[:1]))
    area = abs(shoelace)
    for s in segments:
        if isinstance(s, ada.ArcSegment):
            chord = np.linalg.norm(np.asarray(s.p2, dtype=float)[:2] - np.asarray(s.p1, dtype=float)[:2])
            theta = 2 * math.asin(min(1.0, chord / (2 * s.radius)))
            area += 0.5 * s.radius**2 * (theta - math.sin(theta))
    return area


def _assert_closed(segments):
    for a, b in zip(segments, segments[1:] + segments[:1]):
        assert np.allclose(np.asarray(a.p2, dtype=float)[:2], np.asarray(b.p1, dtype=float)[:2], atol=1e-6)


def test_a_plain_first_corner_before_filleted_second_and_last_corners_is_kept():
    # A rectangle with fillets on two opposite corners, started at a plain corner whose neighbours on both
    # sides are the filleted ones.
    W, H, R = 2.53, 3.48, 0.1
    segments = CurvePoly2d([(0, 0), (0, H, R), (W, H), (W, 0, R)]).segments
    assert len(segments) == 6  # four sides and two fillet arcs; each fillet removes (1 - pi/4) R^2
    _assert_closed(segments)
    assert _outline_area(segments) == pytest.approx(W * H - 2 * (1 - np.pi / 4) * R**2, abs=1e-6)


@pytest.mark.parametrize("reverse", [False, True])
def test_every_start_and_fillet_mask_of_a_rectangle_closes_with_the_right_area(reverse):
    import itertools

    W, H, R = 2.53, 3.48, 0.1
    corners = [(0, 0), (W, 0), (W, H), (0, H)]
    if reverse:
        corners = corners[::-1]
    for mask in itertools.product([False, True], repeat=4):
        pts = [(x, y, R) if m else (x, y) for (x, y), m in zip(corners, mask)]
        expected = W * H - sum(mask) * (4 - np.pi) / 4 * R**2
        for start in range(4):
            rotated = pts[start:] + pts[:start]
            segments = CurvePoly2d(rotated).segments
            ctx = f"mask={mask} start={start} reverse={reverse}"
            assert len(segments) == 4 + sum(mask), ctx
            _assert_closed(segments)
            assert _outline_area(segments) == pytest.approx(expected, abs=1e-6), ctx
