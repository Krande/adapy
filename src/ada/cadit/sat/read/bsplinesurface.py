from __future__ import annotations

from ada import Point
from ada.config import logger
from ada.geom.curve_utils import calculate_multiplicities
from ada.geom.curves import KnotType
from ada.geom.surfaces import (
    BSplineSurfaceForm,
    BSplineSurfaceWithKnots,
    RationalBSplineSurfaceWithKnots,
)


class ACISReferenceDataError(Exception):
    pass


def create_bsplinesurface_from_sat(spline_data_str: str) -> BSplineSurfaceWithKnots | RationalBSplineSurfaceWithKnots:
    head, data = spline_data_str.split("{")

    data_lines = [x.strip() for x in data.splitlines()]
    dline = data_lines[0].split()
    if dline[0] == "ref":
        raise ACISReferenceDataError("Reference data not supported")

    # Check for the extra "0" after "exactsur"
    has_extra_zero = dline[1] == "0"

    # Adjust indices based on whether the "0" is present
    surface_type_idx = 3 if has_extra_zero else 2
    u_degree_idx = 4 if has_extra_zero else 3
    v_degree_idx = 5 if has_extra_zero else 4

    # Surface type: "nurbs", "nubs", or "nullbs"
    surface_type = dline[surface_type_idx]

    if surface_type == "nullbs":
        raise ACISReferenceDataError("Null B-spline surfaces not supported")

    # Degrees in U and V directions
    u_degree = int(dline[u_degree_idx])
    v_degree = int(dline[v_degree_idx])

    # Knot closure type: "open", "closed", or "periodic"
    # u_closure_idx = 7 if has_extra_zero else 5
    # v_closure_idx = 8 if has_extra_zero else 6
    # u_closure = dline[u_closure_idx]
    # v_closure = dline[v_closure_idx]

    # Parse U knot vector (second line)
    u_knot_data = [float(x) for x in data_lines[1].split()]
    u_knots = u_knot_data[0::2]  # U knot values
    u_multiplicities = u_knot_data[1::2]  # U knot multiplicities

    # Parse V knot vector (third line)
    v_knot_data = [float(x) for x in data_lines[2].split()]
    v_knots = v_knot_data[0::2]  # V knot values
    v_multiplicities = v_knot_data[1::2]  # V knot multiplicities

    # Number of control points in U and V directions
    control_points_u = int(sum(u_multiplicities)) + 1 - u_degree
    control_points_v = int(sum(v_multiplicities)) + 1 - v_degree

    # Parse control points starting from the 4th line onward
    control_point_start_line = 3  # Line where control points begin

    # Initialize the control points as a list of tuples (U, V) where each tuple has two control points
    control_points = []
    weights = []

    # create a empty list of control points
    for _ in range(control_points_u):
        control_points.append([None] * control_points_v)

    # create a empty list of weights
    for _ in range(control_points_u):
        weights.append([None] * control_points_v)

    for v in range(control_points_v):
        for u in range(control_points_u):
            u_point_data = [float(x) for x in data_lines[control_point_start_line].split()]
            if len(u_point_data) == 4:
                weights[u][v] = u_point_data[3]
            control_points[u][v] = Point(*u_point_data[:3])
            control_point_start_line += 1

    surf_form = BSplineSurfaceForm.UNSPECIFIED

    num_u_points = len(control_points)
    num_v_points = len(control_points[0])
    u_mult, v_mult = calculate_multiplicities(u_degree, v_degree, u_knots, v_knots, num_u_points, num_v_points)

    if dline[0] == "exactsur":
        logger.info("Exact surface")

    if weights is not None:
        surface = RationalBSplineSurfaceWithKnots(
            u_degree=u_degree,
            v_degree=v_degree,
            control_points_list=control_points,
            surface_form=surf_form,
            u_closed=False,
            v_closed=False,
            self_intersect=False,
            u_multiplicities=u_mult,
            v_multiplicities=v_mult,
            u_knots=u_knots,
            v_knots=v_knots,
            knot_spec=KnotType.UNSPECIFIED,
            weights_data=weights,
        )
    else:
        surface = BSplineSurfaceWithKnots(
            u_degree=u_degree,
            v_degree=v_degree,
            control_points_list=control_points,
            surface_form=surf_form,
            u_knots=u_knots,
            v_knots=v_knots,
            u_multiplicities=u_mult,
            v_multiplicities=v_mult,
            u_closed=False,
            v_closed=False,
            self_intersect=False,
            knot_spec=KnotType.UNSPECIFIED,
        )

    return surface
