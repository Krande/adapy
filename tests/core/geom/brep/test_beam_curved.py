"""BeamCurved carries the analytical curve as its native sweep path."""

import ada
from ada import BeamCurved
from ada.geom import curves as gc
from ada.geom.solids import FixedReferenceSweptAreaSolid


def _spline():
    # a simple degree-2 open B-spline arc through 3 control points
    return gc.BSplineCurveWithKnots(
        degree=2,
        control_points_list=[(0.0, 0.0, 0.0), (0.5, 1.0, 0.0), (1.0, 0.0, 0.0)],
        curve_form=gc.BSplineCurveFormEnum.UNSPECIFIED,
        closed_curve=False,
        self_intersect=False,
        knots=[0.0, 1.0],
        knot_multiplicities=[3, 3],
        knot_spec=gc.KnotType.UNSPECIFIED,
    )


def test_beam_curved_carries_analytical_curve():
    curve = _spline()
    bm = BeamCurved("bm", (0, 0, 0), (1, 0, 0), curve, "HP180x8")
    # the exact curve object is retained — no sampling / degradation
    assert bm.curve3d is curve
    assert isinstance(bm.curve3d, gc.BSplineCurveWithKnots)


def test_beam_curved_solid_is_a_swept_solid_on_the_exact_directrix():
    curve = _spline()
    bm = BeamCurved("bm", (0, 0, 0), (1, 0, 0), curve, "HP180x8")
    geom = bm.solid_geom()
    assert isinstance(geom.geometry, FixedReferenceSweptAreaSolid)
    # the directrix IS the analytical curve, not a polyline approximation
    assert geom.geometry.directrix is curve


def test_beam_curved_is_a_distinct_beam_type():
    curve = _spline()
    bm = BeamCurved("bm", (0, 0, 0), (1, 0, 0), curve, "HP180x8")
    assert isinstance(bm, ada.Beam)
    # exact-type queries separate it from a straight Beam
    assert type(bm) is BeamCurved
