from collections import Counter

import numpy as np

from ada.geom.curves import BSplineCurveFormEnum, BSplineCurveWithKnots, KnotType
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
    knot_spec: KnotType = KnotType.UNSPECIFIED,
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


def calculate_multiplicities(
    u_degree: int, v_degree: int, u_knots: list[float], v_knots: list[float], num_u_points: int, num_v_points: int
) -> tuple[list[int], list[int]]:
    """
    Function to calculate the multiplicities for U and V knot vectors.

    Parameters:
    - u_degree (int): Degree of the B-spline surface in U direction.
    - v_degree (int): Degree of the B-spline surface in V direction.
    - u_knots (list[float]): Knot vector in U direction.
    - v_knots (list[float]): Knot vector in V direction.
    - num_u_points (int): Number of control points in U direction.
    - num_v_points (int): Number of control points in V direction.

    Returns:
    - u_multiplicities (list[int]): List of multiplicities for U knot vector.
    - v_multiplicities (list[int]): List of multiplicities for V knot vector.
    """

    # Ensure that knot vector length matches control points and degree rule
    def complete_knot_vector(knot_vector: list[float], degree: int, num_points: int) -> list[float]:
        expected_knot_length = num_points + degree + 1
        if len(knot_vector) == expected_knot_length:
            return knot_vector
        # Assuming missing knots should be clamped at start and end
        clamped_start = [knot_vector[0]] * degree
        clamped_end = [knot_vector[-1]] * degree
        return clamped_start + knot_vector + clamped_end

    # Calculate the multiplicities
    def calculate_multiplicity(knot_vector: list[float]) -> list[int]:
        knot_counts = Counter(knot_vector)
        return [knot_counts[knot] for knot in sorted(knot_counts)]

    # Complete the knot vectors if necessary
    u_knots_complete = complete_knot_vector(u_knots, u_degree, num_u_points)
    v_knots_complete = complete_knot_vector(v_knots, v_degree, num_v_points)

    # Calculate multiplicities for U and V
    u_multiplicities = calculate_multiplicity(u_knots_complete)
    v_multiplicities = calculate_multiplicity(v_knots_complete)

    return u_multiplicities, v_multiplicities
