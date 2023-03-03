from __future__ import annotations

from ada.concepts.primitives import (
    BSplineSurfaceWithKnots,
    IfcBSplineSurfaceForm,
    RationalBSplineSurfaceWithKnots,
)
from ada.config import get_logger

logger = get_logger()


def create_bsplinesurface_from_sat(spline_data_str: str) -> BSplineSurfaceWithKnots | RationalBSplineSurfaceWithKnots:
    head, data = spline_data_str.split("{")

    data_lines = [x.strip() for x in data.splitlines()]
    dline = data_lines[0].split()
    u_degree, v_degree = [int(float(x)) for x in dline[3:5]]
    surf_form = IfcBSplineSurfaceForm.UNSPECIFIED
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

    props = dict(
        uDegree=u_degree,
        vDegree=v_degree,
        controlPointsList=control_points,
        surfaceForm=surf_form,
        uKnots=uknots,
        vKnots=vknots,
        uMultiplicities=uMult,
        vMultiplicities=vMult,
    )

    if weights is not None:
        surface = RationalBSplineSurfaceWithKnots(**props, weightsData=weights)
    else:
        surface = BSplineSurfaceWithKnots(**props)

    return surface
