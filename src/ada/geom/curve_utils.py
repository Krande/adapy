import numpy as np

from ada.geom.curves import (
    BSplineCurveFormEnum,
    BSplineCurveWithKnots,
    BsplineKnotSpecEnum,
)
from ada.geom.points import Point


def bspline_basis(i, k, t, knots):
    if k == 0:
        return 1 if knots[i] <= t < knots[i + 1] else 0
    else:
        N1 = bspline_basis(i, k - 1, t, knots)
        N2 = bspline_basis(i + 1, k - 1, t, knots)
        a = (t - knots[i]) / (knots[i + k] - knots[i]) if N1 != 0 else 0
        b = (knots[i + k + 1] - t) / (knots[i + k + 1] - knots[i + 1]) if N2 != 0 else 0
        return a * N1 + b * N2


def interpolate_points(points, degree, num_points=100):
    n = len(points) - 1

    knots = [0] * (degree + 1) + list(range(1, n - degree + 1)) + [n - degree + 1] * (degree + 1)
    curve_points = []

    for t in np.linspace(knots[degree], knots[-(degree + 1)], num_points):
        curve_point = np.zeros(3)
        for i in range(n + 1):
            basis = bspline_basis(i, degree, t, knots)
            curve_point += basis * np.array(points[i])

        curve_points.append(tuple(curve_point))

    return curve_points


def create_bspline_curve(
    points: list[Point | tuple[float, float, float]],
    degree: int,
    curve_form: BSplineCurveFormEnum,
    knot_spec: BsplineKnotSpecEnum = BsplineKnotSpecEnum.UNSPECIFIED,
    num_interpolation_points: int = 100,
) -> BSplineCurveWithKnots:
    num_points = len(points)
    knot_multiplicities = [degree + 1] + [1] * (num_points - 2) + [degree + 1]
    knots = [0] * (degree + 1) + list(range(1, num_points - degree)) + [num_points - degree] * (degree + 1)
    control_points = interpolate_points(points, degree, num_points=num_interpolation_points)
    closed_curve = False
    self_intersect = False

    return BSplineCurveWithKnots(
        degree, control_points, curve_form, closed_curve, self_intersect, knot_multiplicities, knots, knot_spec
    )
