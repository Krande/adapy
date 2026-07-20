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
    ArcEdge,
    ArcSegment,
    CurvePoly2d,
    LineSegment,
    SplineEdge,
    SplineSegment,
)
from ada.geom.curves import ArcLine, BSplineCurveWithKnots, IndexedPolyCurve

_SQUARE = [(0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0), (0.0, 1.0, 0.0)]
_ARC = ArcEdge(a=(1.0, 0.0, 0.0), b=(1.0, 1.0, 0.0), midpoint=(1.1, 0.5, 0.0))


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
    reversed_spec = ArcEdge(a=(1.0, 1.0, 0.0), b=(1.0, 0.0, 0.0), midpoint=(1.1, 0.5, 0.0))
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
    spec = SplineEdge(a=(1.0, 0.0, 0.0), b=(1.0, 1.0, 0.0), curve=spline)
    segs = CurvePoly2d.build_edge_segments(_SQUARE, [spec])
    spline_segs = [s for s in segs if isinstance(s, SplineSegment)]
    assert len(spline_segs) == 1
    assert spline_segs[0].curve_geom() is spline


def _bulge_spline() -> BSplineCurveWithKnots:
    """A quadratic B-spline edge from (1,0,0) to (1,1,0) bulging out in +x."""
    from ada.geom.curves import BSplineCurveFormEnum, KnotType

    return BSplineCurveWithKnots(
        degree=2,
        control_points_list=[(1.0, 0.0, 0.0), (1.3, 0.5, 0.0), (1.0, 1.0, 0.0)],
        curve_form=BSplineCurveFormEnum.UNSPECIFIED,
        closed_curve=False,
        self_intersect=False,
        knot_multiplicities=[3, 3],
        knots=[0.0, 1.0],
        knot_spec=KnotType.UNSPECIFIED,
    )


def _spline_plate() -> Plate:
    segs = CurvePoly2d.build_edge_segments(
        _SQUARE, [SplineEdge(a=(1.0, 0.0, 0.0), b=(1.0, 1.0, 0.0), curve=_bulge_spline())]
    )
    return Plate.from_segments("sp", segs, 0.05)


def test_from_segments_keeps_the_spline_analytic_in_curve_geom():
    cg = _spline_plate().poly.curve_geom(use_3d_segments=False)
    assert isinstance(cg, IndexedPolyCurve)
    splines = [s for s in cg.segments if isinstance(s, BSplineCurveWithKnots)]
    assert len(splines) == 1
    # 2D-local (extruded profile plane), endpoints snapped to the corners so the outline wire closes.
    assert np.allclose(splines[0].control_points_list[0], (1.0, 0.0)) or np.allclose(
        splines[0].control_points_list[0], (1.0, 0.0, 0.0)
    )


def test_spline_plate_ifc_outer_curve_is_a_composite_with_a_bspline():
    import ifcopenshell

    from ada.cadit.ifc.write.geom.surfaces import arbitrary_profile_def

    f = ifcopenshell.file(schema="IFC4X3")
    oc = arbitrary_profile_def(_spline_plate().solid_geom().geometry.swept_area, f).OuterCurve
    assert oc.is_a("IfcCompositeCurve")
    kinds = [s.ParentCurve.is_a() for s in oc.Segments]
    assert "IfcBSplineCurveWithKnots" in kinds


def test_spline_plate_builds_an_occ_solid_that_bulges():
    from ada.cad import active_backend

    be = active_backend()
    solid = _spline_plate().solid_occ()
    assert be.is_valid(solid)
    # A flat unit square x 0.05 thick = 0.05; the +x bulge must add volume (analytic curve, not chord).
    assert be.volume(solid) > 0.05 + 1e-4


def test_spline_plate_get_unique_samples_the_spline_to_a_polyline():
    cg = _spline_plate().poly.curve_geom(use_3d_segments=False)
    _pts, seg_idx = cg.get_unique_points_and_segment_indices()
    # Three straight edges (2 indices each) + one sampled spline (a >3-index polyline).
    assert sorted(len(i) for i in seg_idx)[-1] > 3


def _degree1_spline(p0=(0.0, 0.0, 0.0), p1=(2.0, 0.0, 0.0)) -> BSplineCurveWithKnots:
    return BSplineCurveWithKnots(
        degree=1,
        control_points_list=[p0, p1],
        curve_form=None,
        closed_curve=False,
        self_intersect=False,
        knot_multiplicities=[2, 2],
        knots=[0.0, 1.0],
        knot_spec=None,
    )


def test_bspline_curve_sample_is_the_sampler_home():
    """A degree-1 B-spline between two points samples to evenly spaced points along the line."""
    pts = np.asarray(_degree1_spline().sample(5))
    assert pts.shape == (5, 3)
    assert np.allclose(pts[:, 0], np.linspace(0.0, 2.0, 5))
    assert np.allclose(pts[:, 1:], 0.0)


def test_spline_segment_sample_delegates_to_the_curve():
    spline = _degree1_spline()
    seg = SplineSegment((0.0, 0.0, 0.0), (2.0, 0.0, 0.0), curve=spline)
    pts = seg.sample(5)
    assert len(pts) == 5
    assert np.allclose([p[0] for p in pts], np.linspace(0.0, 2.0, 5))


def test_bspline_sample_raises_on_malformed_spec():
    bad = BSplineCurveWithKnots(
        degree=3,  # degree >= number of control points => malformed
        control_points_list=[(0.0, 0.0, 0.0), (1.0, 0.0, 0.0)],
        curve_form=None,
        closed_curve=False,
        self_intersect=False,
        knot_multiplicities=[2, 2],
        knots=[0.0, 1.0],
        knot_spec=None,
    )
    with pytest.raises(ValueError):
        bad.sample(5)
