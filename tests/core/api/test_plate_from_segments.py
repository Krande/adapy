"""``Plate.from_segments`` / ``CurvePoly2d.from_segments`` build a plate outline from an ordered list of
line/arc/spline segments, carrying analytic curved edges verbatim instead of sampling them into points.

This is the path the ACIS/SAT plate reader uses for circle/ellipse boundary edges: it emits
``PlateEdgeCurve`` specs, ``build_edge_segments`` turns ordered corners + specs into real segments, and
``from_segments`` keeps them analytic (exact in IFC/STEP, discretized only downstream at tessellation).
"""

from __future__ import annotations

import numpy as np
import pytest

from ada import Plate
from ada.api.curves import (
    ArcSegment,
    CurvePoly2d,
    LineSegment,
    PlateEdgeCurve,
    SplineSegment,
)
from ada.geom.curves import ArcLine, BSplineCurveWithKnots, IndexedPolyCurve

_SQUARE = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
_ARC = PlateEdgeCurve("arc", a=(1.0, 0.0, 0.0), b=(1.0, 1.0, 0.0), midpoint=(1.1, 0.5, 0.0))


def test_build_edge_segments_marks_only_the_arc_edge():
    segs = CurvePoly2d.build_edge_segments(_SQUARE, [_ARC])
    assert [type(s).__name__ for s in segs] == ["LineSegment", "ArcSegment", "LineSegment", "LineSegment"]
    arc = segs[1]
    assert np.allclose(arc.midpoint, (1.1, 0.5, 0.0))
    # loop is closed: each segment's end is the next segment's start
    for i in range(len(segs)):
        assert np.allclose(segs[i].p2, segs[(i + 1) % len(segs)].p1)


def test_build_edge_segments_is_winding_agnostic():
    """A spec whose a/b are reversed relative to the corner order still matches its edge."""
    reversed_spec = PlateEdgeCurve("arc", a=(1.0, 1.0, 0.0), b=(1.0, 0.0, 0.0), midpoint=(1.1, 0.5, 0.0))
    segs = CurvePoly2d.build_edge_segments(_SQUARE, [reversed_spec])
    assert sum(isinstance(s, ArcSegment) for s in segs) == 1


def test_from_segments_keeps_the_arc_analytic_in_2d_and_3d():
    poly = CurvePoly2d.from_segments(CurvePoly2d.build_edge_segments(_SQUARE, [_ARC]))
    assert sum(isinstance(s, ArcSegment) for s in poly.segments3d) == 1
    assert sum(isinstance(s, ArcSegment) for s in poly.segments) == 1
    assert np.allclose(
        poly.segments3d[[isinstance(s, ArcSegment) for s in poly.segments3d].index(True)].midpoint, (1.1, 0.5, 0.0)
    )
    # seg_index inserts the arc midpoint => one segment has 3 indices, the rest 2
    assert sorted(len(ix) for ix in poly.seg_index) == [2, 2, 2, 3]


def test_from_segments_curve_geom_emits_an_arcline():
    poly = CurvePoly2d.from_segments(CurvePoly2d.build_edge_segments(_SQUARE, [_ARC]))
    cg = poly.curve_geom(use_3d_segments=False)
    assert isinstance(cg, IndexedPolyCurve)
    assert any(isinstance(s, ArcLine) for s in cg.segments)


def test_plate_from_segments_builds_an_extruded_solid():
    plate = Plate.from_segments("p", CurvePoly2d.build_edge_segments(_SQUARE, [_ARC]), 0.01)
    sg = plate.solid_geom()
    assert type(sg.geometry).__name__ == "ExtrudedAreaSolid"
    assert sum(isinstance(s, ArcSegment) for s in plate.poly.segments3d) == 1


def test_plate_from_segments_flip_normal_reverses_the_plate_normal():
    segs = CurvePoly2d.build_edge_segments(_SQUARE, [_ARC])
    n0 = np.asarray(Plate.from_segments("p", segs, 0.01).poly.normal, dtype=float)
    n1 = np.asarray(Plate.from_segments("p", segs, 0.01, flip_normal=True).poly.normal, dtype=float)
    assert np.allclose(n0, -n1)


def test_from_segments_requires_at_least_three_segments():
    a, b = (0.0, 0.0, 0.0), (1.0, 0.0, 0.0)
    with pytest.raises(ValueError):
        CurvePoly2d.from_segments([LineSegment(a, b), LineSegment(b, a)])


def test_build_edge_segments_carries_a_spline_edge():
    spline = BSplineCurveWithKnots(
        degree=1,
        control_points_list=[(1.0, 0.0, 0.0), (1.0, 1.0, 0.0)],
        curve_form=None,
        closed_curve=False,
        self_intersect=False,
        knot_multiplicities=[2, 2],
        knots=[0.0, 1.0],
        knot_spec=None,
    )
    spec = PlateEdgeCurve("spline", a=(1.0, 0.0, 0.0), b=(1.0, 1.0, 0.0), curve=spline)
    segs = CurvePoly2d.build_edge_segments(_SQUARE, [spec])
    spline_segs = [s for s in segs if isinstance(s, SplineSegment)]
    assert len(spline_segs) == 1
    assert spline_segs[0].curve_geom() is spline
