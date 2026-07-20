"""Equivalence guard for the vectorised ``_bspline_points`` B-spline sampler.

``_bspline_points`` (ada.cadit.sat.read.plate_edge_curves) is the hot path of the Genie-XML / SAT
plate-edge read (``_de_boor`` was ~4.7s of a single large Genie-XML import). It was a pure-Python
per-point de
Boor loop; the vectorised version must produce byte-for-byte-equivalent samples so plate geometry is
unchanged. This test pins the vectorised output against an independent scalar de Boor reference (a
copy of the original loop) plus a hand-computed quadratic Bézier golden.
"""

import numpy as np

from ada.cadit.sat.read.plate_edge_curves import _bspline_points, _de_boor
from ada.geom.curves import BSplineCurveWithKnots, RationalBSplineCurveWithKnots


def _Curve(degree, control_points, knots, mults, weights=None):
    """A real ``BSplineCurveWithKnots`` (or rational subclass), whose ``sample`` ``_bspline_points`` wraps."""
    common = dict(
        degree=degree,
        control_points_list=control_points,
        curve_form=None,
        closed_curve=False,
        self_intersect=False,
        knot_multiplicities=mults,
        knots=knots,
        knot_spec=None,
    )
    if weights is not None:
        return RationalBSplineCurveWithKnots(weights_data=weights, **common)
    return BSplineCurveWithKnots(**common)


def _scalar_bspline_points(curve, n):
    """The original per-point scalar implementation, kept here as the reference."""
    cp = np.asarray([list(p)[:3] for p in curve.control_points_list], dtype=float)
    knots = np.repeat(np.asarray(curve.knots, dtype=float), np.asarray(curve.knot_multiplicities, dtype=int))
    deg = int(curve.degree)
    w = getattr(curve, "weights_data", None) or getattr(curve, "weights", None)
    if w is not None and len(w) == len(cp):
        wa = np.asarray(w, dtype=float).reshape(-1, 1)
        cp = np.hstack([cp * wa, wa])
    lo, hi = float(knots[deg]), float(knots[len(knots) - deg - 1])
    out = []
    for x in np.linspace(lo, hi, n):
        p = _de_boor(float(x), knots, cp, deg)
        if p.shape[0] == 4:
            p = p[:3] / p[3]
        out.append(tuple(float(v) for v in p[:3]))
    return out


# (name, curve, n)
_CASES = [
    (
        "bezier_deg2",
        _Curve(2, [(0, 0, 0), (1, 2, 0), (2, 0, 0)], [0.0, 1.0], [3, 3]),
        7,
    ),
    (
        "clamped_deg2_4cp",
        _Curve(2, [(0, 0, 0), (1, 3, 1), (3, 3, -1), (4, 0, 0.5)], [0.0, 1.0, 2.0], [3, 1, 3]),
        13,
    ),
    (
        "clamped_deg3_5cp",
        _Curve(3, [(0, 0, 0), (1, 2, 0), (2, -1, 1), (3, 2, 0), (4, 0, 0)], [0.0, 0.5, 1.0], [4, 1, 4]),
        17,
    ),
    (
        "rational_deg2_quarter_circle",
        _Curve(2, [(1, 0, 0), (1, 1, 0), (0, 1, 0)], [0.0, 1.0], [3, 3], weights=[1.0, np.sqrt(0.5), 1.0]),
        11,
    ),
]


def test_vectorised_matches_scalar_reference():
    for name, curve, n in _CASES:
        got = _bspline_points(curve, n)
        ref = _scalar_bspline_points(curve, n)
        assert got is not None, name
        assert np.allclose(np.asarray(got), np.asarray(ref), rtol=0, atol=1e-9), name


def test_quadratic_bezier_golden():
    # Quadratic Bézier P0=(0,0,0) P1=(1,2,0) P2=(2,0,0). At t: (1-t)^2 P0 + 2t(1-t) P1 + t^2 P2.
    curve = _Curve(2, [(0, 0, 0), (1, 2, 0), (2, 0, 0)], [0.0, 1.0], [3, 3])
    pts = _bspline_points(curve, 3)  # t = 0, 0.5, 1
    expected = [(0.0, 0.0, 0.0), (1.0, 1.0, 0.0), (2.0, 0.0, 0.0)]
    assert np.allclose(np.asarray(pts), np.asarray(expected), atol=1e-12)


def test_rational_weights_lie_on_unit_circle():
    # Standard rational quadratic Bézier for a quarter circle: every sample has radius 1.
    curve = _Curve(2, [(1, 0, 0), (1, 1, 0), (0, 1, 0)], [0.0, 1.0], [3, 3], weights=[1.0, np.sqrt(0.5), 1.0])
    pts = np.asarray(_bspline_points(curve, 9))
    radii = np.linalg.norm(pts[:, :2], axis=1)
    assert np.allclose(radii, 1.0, atol=1e-9)
