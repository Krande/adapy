from __future__ import annotations

from ada import Point
from ada.config import logger
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
    u_degree, v_degree = [int(float(x)) for x in dline[3:5]]

    surf_form = BSplineSurfaceForm.UNSPECIFIED
    uknots_in = [float(x) for x in data_lines[1].split()]
    uknots = uknots_in[0::2]
    uMult = [int(x) for x in uknots_in[1::2]]

    vknots_in = [float(x) for x in data_lines[2].split()]
    vknots = vknots_in[0::2]

    vMult = [int(x) for x in vknots_in[1::2]]
    res = [[float(i) for i in x.split()] for x in data_lines[3 : 3 + v_degree * 4]]
    control_points = []
    for i in range(0, v_degree):
        control_points += [res[i::3]]

    weights = None
    if len(control_points[0]) == 4:
        weights = [[i[-1] for i in x] for x in control_points]

    if dline[0] == "exactsur":
        logger.info("Exact surface")

    ctrl_points = [[Point(*p[0:3]) for p in x] for x in control_points]

    if weights is not None:
        surface = RationalBSplineSurfaceWithKnots(
            u_degree=u_degree,
            v_degree=v_degree,
            control_points_list=ctrl_points,
            surface_form=surf_form,
            u_closed=False,
            v_closed=False,
            self_intersect=False,
            u_multiplicities=uMult,
            v_multiplicities=vMult,
            u_knots=uknots,
            v_knots=vknots,
            knot_spec=KnotType.UNSPECIFIED,
            weights_data=weights,
        )
    else:
        surface = BSplineSurfaceWithKnots(
            u_degree=u_degree,
            v_degree=v_degree,
            control_points_list=ctrl_points,
            surface_form=surf_form,
            u_knots=uknots,
            v_knots=vknots,
            u_multiplicities=uMult,
            v_multiplicities=vMult,
            u_closed=False,
            v_closed=False,
            self_intersect=False,
            knot_spec=KnotType.UNSPECIFIED,
        )

    return surface
