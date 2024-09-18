from __future__ import annotations

from ada.cadit.sat.read.bsplinesurface import ACISReferenceDataError
from ada.cadit.sat.read.sat_entities import AcisRecord
from ada.config import logger
from ada.geom.curves import BSplineCurveWithKnots, RationalBSplineCurveWithKnots, BSplineCurveFormEnum, KnotType


class ACISIncompleteCtrlPoints(Exception):
    pass


def extract_data_lines(data: str) -> list[str]:
    data_lines = []
    for x in data.splitlines():
        line_data = x.strip()
        if not line_data:
            continue
        if '}' in x:
            break
        data_lines.append(line_data)
    return data_lines

def get_curve_type(dline: list[str], has_extra_zero) -> str:
    # Adjust indices based on whether the "0" is present
    curve_type_idx = 3 if has_extra_zero else 2

    # Curve type: "nurbs", "nubs", or "nullbs"
    return dline[curve_type_idx]

def get_degree_and_closure(dline: list[str], has_extra_zero) -> tuple[int, bool]:
    # Adjust indices based on whether the "0" is present
    u_degree_idx = 4 if has_extra_zero else 3
    u_closure_idx = 5 if has_extra_zero else 4

    degree = int(dline[u_degree_idx])
    closed_curve = False if dline[u_closure_idx] == "open" else True

    return degree, closed_curve

def create_bspline_curve_from_lawintcur(data_lines: list[str]) -> BSplineCurveWithKnots | None:
    """Create a B-spline curve from a lawintcur data string."""

    # Extract degree and closed/open status
    dline = data_lines[0].split()

    if dline[0] == "ref":
        raise ACISReferenceDataError("Reference data not supported")

    has_extra_zero = dline[1] != "full"

    spl_type = dline[0]
    logger.info(f"Creating B-spline curve of type {spl_type}")

    # Curve type: "nurbs", "nubs", or "nullbs"
    curve_type = get_curve_type(dline, has_extra_zero)
    if curve_type == "nullbs":
        raise ACISReferenceDataError("Null B-spline surfaces not supported")

    degree, closed_curve = get_degree_and_closure(dline, has_extra_zero)

    # Extract knots and multiplicities
    knots_in = [float(x) for x in data_lines[1].split()]
    ctrl_point_line_idx = 2
    if len(data_lines[2].split()) != 3:
        knots_in += [float(x) for x in data_lines[2].split()]
        ctrl_point_line_idx = 3

    knots = knots_in[0::2]
    mult = [int(x) for x in knots_in[1::2]]

    # Adjust knot multiplicities to satisfy IFC requirements
    mult[0] = degree + 1  # Start multiplicity
    mult[-1] = degree + 1  # End multiplicity
    total_knots = sum(mult)
    num_control_points = total_knots - degree - 1

    # Extract control points
    control_point_lines = data_lines[ctrl_point_line_idx:+ctrl_point_line_idx + num_control_points]
    control_points = []
    for line in control_point_lines:
        if line.strip() == "0":  # End of control points
            break
        lsplit = line.split()
        if len(lsplit) < 3:
            raise ACISIncompleteCtrlPoints("Incomplete control point data: {}".format(line))

        values = [float(i) for i in lsplit]
        control_points.append(values)

    if len(control_points) != num_control_points:
        raise ACISIncompleteCtrlPoints(
            "Mismatch in number of control points. Expected {}, got {}.".format(num_control_points, len(control_points))
        )

    # Extract weights if present
    weights = None
    if len(control_points[0]) == 4:
        weights = [cp[-1] for cp in control_points]
        control_points = [cp[:3] for cp in control_points]

    # Create the B-spline curve
    if weights:
        curve = RationalBSplineCurveWithKnots(
            degree=degree,
            control_points_list=control_points,
            curve_form=BSplineCurveFormEnum.UNSPECIFIED,
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
            curve_form=BSplineCurveFormEnum.UNSPECIFIED,
            closed_curve=closed_curve,
            self_intersect=False,
            knots=knots,
            knot_multiplicities=mult,
            knot_spec=KnotType.UNSPECIFIED,
        )
    return curve


def create_bspline_curve_from_exactcur(data_lines: list[str]) -> BSplineCurveWithKnots | None:
    """Create a B-spline curve from an exact_sur data string."""
    dline = data_lines[0].split()
    should_bump = dline[1] != "full"

    # Curve type: "nurbs", "nubs", or "nullbs"
    curve_type = get_curve_type(dline, should_bump)
    if curve_type == "nullbs":
        raise ACISReferenceDataError("Null B-spline surfaces not supported")

    degree, closed_curve = get_degree_and_closure(dline, should_bump)

    # Extract knots and multiplicities
    knots_in = [float(x) for x in data_lines[1].split()]
    knots = knots_in[0::2]
    mult = [int(x) for x in knots_in[1::2]]

    # Adjust knot multiplicities to satisfy IFC requirements
    mult[0] = degree + 1  # Start multiplicity
    mult[-1] = degree + 1  # End multiplicity
    total_knots = sum(mult)
    num_control_points = total_knots - degree - 1

    # Extract control points
    control_point_lines = data_lines[2:]
    control_points = []
    for line in control_point_lines:
        if line.strip() == "0":  # End of control points
            break
        lsplit = line.split()
        if len(lsplit) < 3:
            logger.warning("Incomplete control point data: {}".format(line))
            break
        values = [float(i) for i in lsplit]

        control_points.append(values)

    if len(control_points) != num_control_points:
        logger.error(
            "Mismatch in number of control points. Expected {}, got {}.".format(num_control_points, len(control_points))
        )
        return None

    # Extract weights if present
    weights = None
    if len(control_points[0]) == 4:
        weights = [cp[-1] for cp in control_points]
        control_points = [cp[:3] for cp in control_points]

    # Create the B-spline curve
    if weights:
        curve = RationalBSplineCurveWithKnots(
            degree=degree,
            control_points_list=control_points,
            curve_form=BSplineCurveFormEnum.UNSPECIFIED,
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
            curve_form=BSplineCurveFormEnum.UNSPECIFIED,
            closed_curve=closed_curve,
            self_intersect=False,
            knots=knots,
            knot_multiplicities=mult,
            knot_spec=KnotType.UNSPECIFIED,
        )
    return curve


def create_bspline_curve_from_sat(spline_record: AcisRecord) -> BSplineCurveWithKnots | None:
    spline_data_str = spline_record.get_as_string()
    split_data = spline_data_str.split("{", 1)
    if len(split_data) < 2:
        logger.error("Invalid spline data format")
        return None

    data = split_data[1].strip("}").strip()
    if data.startswith("ref"):
        raise ACISReferenceDataError("Reference data not supported")

    data_lines = extract_data_lines(data)
    dline = data_lines[0].split()

    if dline[0] == "ref":
        raise ACISReferenceDataError("Reference data not supported")

    spl_type = dline[0]
    if spl_type == "lawintcur":
        return create_bspline_curve_from_lawintcur(data_lines)
    elif spl_type == "exactcur":
        return create_bspline_curve_from_exactcur(data_lines)
    else:
        raise ACISReferenceDataError(f"Unsupported spline type: {spl_type}")
