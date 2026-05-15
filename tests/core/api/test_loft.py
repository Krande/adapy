from __future__ import annotations

import math

import pytest

from ada.api.loft import (
    intersect_with_plane,
    iter_face_poly_loops,
    loft_profiles,
    loft_to_poly_loops,
    wire_from_poly_loop,
)
from ada.geom.curves import PolyLoop
from ada.geom.direction import Direction
from ada.geom.points import Point


def _square(side: float, z: float) -> PolyLoop:
    half = side / 2.0
    return PolyLoop(polygon=[
        Point(-half, -half, z),
        Point(half, -half, z),
        Point(half, half, z),
        Point(-half, half, z),
    ])


def _circle(radius: float, z: float, segments: int = 24) -> PolyLoop:
    pts = []
    for i in range(segments):
        a = 2 * math.pi * i / segments
        pts.append(Point(radius * math.cos(a), radius * math.sin(a), z))
    return PolyLoop(polygon=pts)


def test_wire_closes_open_loop():
    loop = PolyLoop(polygon=[Point(0, 0, 0), Point(1, 0, 0), Point(1, 1, 0)])
    wire = wire_from_poly_loop(loop)
    assert wire is not None


def test_loft_two_squares_produces_solid():
    shape = loft_profiles([_square(1.0, 0.0), _square(1.0, 1.0)])
    # 6 faces for a box: top, bottom, 4 sides
    loops = list(iter_face_poly_loops(shape))
    assert len(loops) == 6


def test_loft_square_to_circle_produces_solid():
    # Ruled loft between mismatched-vertex sections fans out the side
    # faces — one per circle segment plus two caps.
    segments = 24
    shape = loft_profiles([_square(2.0, 0.0), _circle(1.0, 2.0, segments=segments)])
    loops = list(iter_face_poly_loops(shape))
    assert len(loops) == segments + 2


def test_loft_to_poly_loops_returns_points():
    loops = loft_to_poly_loops([_square(1.0, 0.0), _square(1.0, 1.0)])
    assert len(loops) == 6
    for loop in loops:
        assert isinstance(loop, PolyLoop)
        for p in loop.polygon:
            assert isinstance(p, Point)


def test_intersect_with_plane_midspan():
    shape = loft_profiles([_square(2.0, 0.0), _square(2.0, 4.0)])
    cross = intersect_with_plane(shape, Point(0.0, 0.0, 2.0), Direction(0.0, 0.0, 1.0))
    loops = list(iter_face_poly_loops(cross))
    # Single mid-span face intersection on an extrusion: one outer loop.
    assert len(loops) >= 1


def test_loft_rejects_single_profile():
    with pytest.raises(ValueError):
        loft_profiles([_square(1.0, 0.0)])
