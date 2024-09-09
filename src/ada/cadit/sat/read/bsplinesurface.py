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


def create_bsplinesurface_from_sat(spline_data_str: str) -> BSplineSurfaceWithKnots | RationalBSplineSurfaceWithKnots:
    head, data = spline_data_str.split("{")

    data_lines = [x.strip() for x in data.splitlines()]
    dline = data_lines[0].split()
    if dline[1] == "full":
        u_deg_idx = 3
        v_deg_idx = 4
    else: # dline[1] == "0"
        u_deg_idx = 4
        v_deg_idx = 5

    # Grid size in the U and V directions
    grid_u_len = int(float(dline[-2]))  # Number of control points in U direction
    grid_v_len = int(float(dline[-1]))  # Number of control points in V direction

    # Degrees in U and V directions
    u_degree = int(float(dline[u_deg_idx]))
    v_degree = int(float(dline[v_deg_idx]))

    # Extract knot vectors
    u_knots_in = [float(x) for x in data_lines[1].split()]
    u_knots = u_knots_in[0::2]  # U knot vector (every second value)
    v_knots_in = [float(x) for x in data_lines[2].split()]
    v_knots = v_knots_in[0::2]  # V knot vector (every second value)


    # Calculate the number of control points in U and V directions
    control_points_u = len(u_knots) - u_degree - 1
    control_points_v = len(v_knots) - v_degree - 1

    # Total number of control points
    total_control_points = control_points_u * control_points_v


    v_start, v_end = 3 , 3 + v_degree * u_degree
    u_start, u_end = v_end, v_end + v_degree * u_degree

    v_start_data = data_lines[v_start : v_end]
    u_start_data = data_lines[u_start : u_end]
    res_v = [[float(i) for i in x.split()] for x in v_start_data]
    res_u = [[float(i) for i in x.split()] for x in u_start_data]
    control_points = [[Point(*v[:3]),Point(*u[:3])] for u,v in zip(res_u, res_v)]

    surf_form = BSplineSurfaceForm.UNSPECIFIED


    num_u_points = len(control_points)
    num_v_points = len(control_points[0])
    u_mult, v_mult = calculate_multiplicities(u_degree, v_degree, u_knots, v_knots, num_u_points, num_v_points)

    weights = None
    if len(res_v[0]) == 4:
        weights = [[i[-1] for i in x] for x in zip(res_u, res_v)]

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
