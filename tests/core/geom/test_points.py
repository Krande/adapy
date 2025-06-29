from ada.geom.direction import Direction
from ada.geom.points import Point


def test_point_equality():
    p1 = Point(0, 0, 0)
    p2 = Point(0, 0, 0)
    p3 = Point(1, 0, 0)
    assert p1.is_equal(p2)
    assert not p1.is_equal(p3)


def test_direction_equality():
    d1 = Direction(0, 0, 0)
    d2 = Direction(0, 0, 0)
    d3 = Direction(1, 0, 0)
    assert d1.is_equal(d2)
    assert not d1.is_equal(d3)
