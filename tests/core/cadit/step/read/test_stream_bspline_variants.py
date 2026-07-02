"""STEP B-spline subtype coverage: BEZIER / UNIFORM / QUASI_UNIFORM curves & surfaces.

These subtypes omit explicit knots (implied by degree + control-point count per
ISO 10303-42). The reader must still import them into the native (Rational)BSpline geom
so no geometry is left behind. Tests pin the implicit-knot computation and the builders.
"""

from __future__ import annotations

from ada.cadit.step.read import stream_reader as sr
from ada.geom.surfaces import BSplineSurfaceWithKnots


class _IdResolver:
    """deref returns the value unchanged — control points are passed in directly."""

    def deref(self, x):
        return x


_FORM = sr._Enum("UNSPECIFIED")
_F = sr._Enum("F")


def test_implicit_knots_bezier():
    # degree 2, 3 control points -> single Bezier segment: knots [0,1], mults [3,3]
    k, m = sr._implicit_bspline_knots("PIECEWISE_BEZIER_KNOTS", 2, 3)
    assert k == [0.0, 1.0] and m == [3, 3]
    assert sum(m) == 3 + 2 + 1  # n + degree + 1


def test_implicit_knots_quasi_uniform():
    # degree 2, 5 cps -> clamped ends + 2 uniform interior knots
    k, m = sr._implicit_bspline_knots("QUASI_UNIFORM_KNOTS", 2, 5)
    assert m == [3, 1, 1, 3] and len(k) == 4
    assert sum(m) == 5 + 2 + 1


def test_implicit_knots_uniform():
    # degree 2, 4 cps -> open uniform, every knot mult 1, n+deg+1 knots
    k, m = sr._implicit_bspline_knots("UNIFORM_KNOTS", 2, 4)
    assert m == [1] * 7 and len(k) == 7
    assert sum(m) == 4 + 2 + 1


def test_bezier_curve_imports_to_bspline():
    cps = [(0.0, 0.0, 0.0), (1.0, 1.0, 0.0), (2.0, 0.0, 0.0)]
    # BEZIER_CURVE('', degree, (cps), form, closed, si)
    crv = sr._b_bezier_curve(_IdResolver(), ["", 2, cps, _FORM, _F, _F])
    assert type(crv).__name__ == "BSplineCurveWithKnots"
    assert crv.degree == 2
    assert crv.knots == [0.0, 1.0] and crv.knot_multiplicities == [3, 3]
    assert len(crv.control_points_list) == 3


def test_uniform_curve_imports_to_bspline():
    cps = [(0.0, 0.0, 0.0), (1.0, 1.0, 0.0), (2.0, 0.0, 0.0), (3.0, 1.0, 0.0)]
    crv = sr._b_uniform_curve(_IdResolver(), ["", 2, cps, _FORM, _F, _F])
    assert type(crv).__name__ == "BSplineCurveWithKnots"
    assert crv.knot_multiplicities == [1] * 7


def test_bezier_surface_imports_to_bspline():
    grid = [
        [(0.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 2.0, 0.0)],
        [(1.0, 0.0, 1.0), (1.0, 1.0, 1.0), (1.0, 2.0, 1.0)],
        [(2.0, 0.0, 0.0), (2.0, 1.0, 0.0), (2.0, 2.0, 0.0)],
    ]
    # BEZIER_SURFACE('', u_deg, v_deg, (grid), form, u_closed, v_closed, si)
    surf = sr._b_bezier_surface(_IdResolver(), ["", 2, 2, grid, _FORM, _F, _F, _F])
    assert isinstance(surf, BSplineSurfaceWithKnots)
    assert surf.u_degree == 2 and surf.v_degree == 2
    assert surf.u_knots == [0.0, 1.0] and surf.u_multiplicities == [3, 3]
    assert surf.v_multiplicities == [3, 3]


def test_variant_types_registered():
    for t in (
        "BEZIER_CURVE",
        "UNIFORM_CURVE",
        "QUASI_UNIFORM_CURVE",
        "BEZIER_SURFACE",
        "UNIFORM_SURFACE",
        "QUASI_UNIFORM_SURFACE",
    ):
        assert t in sr._BUILDERS
