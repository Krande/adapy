import ada
import ada.geom.curves as geo_cu


def test_line_segment2d():
    line2d = ada.LineSegment((0, 0), (1, 1))
    curve_geom = line2d.curve_geom()
    assert isinstance(curve_geom, geo_cu.Line)
    assert line2d.p1.is_equal(curve_geom.start)
    assert line2d.p2.is_equal(curve_geom.end)


def test_line_segment3d():
    line3d = ada.LineSegment((0, 0, 0), (1, 1, 1))
    curve_geom = line3d.curve_geom()
    assert isinstance(curve_geom, geo_cu.Line)
    assert line3d.p1.is_equal(curve_geom.start)
    assert line3d.p2.is_equal(curve_geom.end)


def test_arc_line_2d_from_midpoint():
    arc = ada.ArcSegment((0, 0), (1, 1), (0.5, 1.2))
    curve_geom = arc.curve_geom()
    assert isinstance(curve_geom, geo_cu.ArcLine)
    assert arc.p1.is_equal(curve_geom.start)
    assert arc.p2.is_equal(curve_geom.end)
    assert arc.midpoint.is_equal(curve_geom.midpoint)
