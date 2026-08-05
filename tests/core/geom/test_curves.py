import ada
import ada.geom.curves as geo_cu
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

    # An even shorter leg (0.05 m) used to crash on a None arc midpoint — must not.
    tight = [ada.Point(*p) for p in [(0, 0, 0), (1, 0, 0), (1, 0.05, 0), (2, 0.05, 0)]]
    tight_segments = segments3d_from_points3d(tight, radius=0.3)
    for a, b in zip(tight_segments[:-1], tight_segments[1:]):
        assert a.p2.is_equal(b.p1)
