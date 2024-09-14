from ada.config import logger
from ada.geom.curves import (
    BSplineCurveFormEnum,
    BSplineCurveWithKnots,
    KnotType,
    RationalBSplineCurveWithKnots,
)


def create_bspline_curve_from_sat(spline_data_str: str) -> BSplineCurveWithKnots:
    split_data = spline_data_str.split("{")
    # head, data = spline_data_str.split("{")
    # head = split_data[0]
    data = split_data[1]

    data_lines = [x.strip() for x in data.splitlines()]
    dline = data_lines[0].split()
    degree = int(dline[3])
    curve_form = BSplineCurveFormEnum.UNSPECIFIED
    closed_curve = False if dline[4] == "open" else True
    knots_in = [float(x) for x in data_lines[1].split()]
    knots = knots_in[0::2]
    mult = [int(x) for x in knots_in[1::2]]
    # ctrl_p = data_lines[2 : 2 + (degree + 1)]

    control_points = [[float(i) for i in x.split()] for x in data_lines[2 : 2 + (degree + 1)]]

    weights = None
    if len(control_points[0]) == 4:
        weights = [x[-1] for x in control_points]
        control_points = [x[:3] for x in control_points]

    if dline[0] == "exactcur":
        logger.info("Exact curve")

    if weights is not None:
        curve = RationalBSplineCurveWithKnots(
            degree=degree,
            control_points_list=control_points,
            curve_form=curve_form,
            closed_curve=closed_curve,
            self_intersect=False,
            knots=knots,
            knot_multiplicities=mult,
            knot_spec=KnotType.UNSPECIFIED,
            weights_data=weights,
        )
    else:
        curve = BSplineCurveWithKnots(
            degree=degree,
            control_points_list=control_points,
            curve_form=curve_form,
            closed_curve=closed_curve,
            self_intersect=False,
            knots=knots,
            knot_multiplicities=mult,
            knot_spec=KnotType.UNSPECIFIED,
        )

    return curve
