from __future__ import annotations

from typing import Iterable

from ada import Point
from ada.cadit.sat.read.sat_entities import AcisRecord
from ada.config import Config, logger
from ada.geom import curves as geo_cu
from ada.geom.curves import (
    BSplineCurveFormEnum,
    BSplineCurveWithKnots,
    KnotType,
    RationalBSplineCurveWithKnots,
)


def create_bspline_curve_from_sat(spline_record: AcisRecord) -> BSplineCurveWithKnots:
    spline_data_str = spline_record.get_as_string()
    split_data = spline_data_str.split("{", 1)
    if len(split_data) < 2:
        logger.error("Invalid spline data format")
        return None
    data = split_data[1].strip("}").strip()

    data_lines = [x.strip() for x in data.splitlines() if x.strip()]
    if not data_lines:
        logger.error("No data lines found in spline data")
        return None

    # Extract degree and closed/open status
    dline = data_lines[0].split()
    if len(dline) < 5:
        logger.error("Invalid spline header line: {}".format(dline))
        return None

    degree = int(dline[3])
    closed_curve = False if dline[4] == "open" else True

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
        values = [float(i) for i in line.split()]
        if len(values) < 3:
            logger.warning("Incomplete control point data: {}".format(line))
            continue
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


def get_edge(coedge: AcisRecord) -> geo_cu.OrientedEdge:
    sat_store = coedge.sat_store

    # Coedge indices
    edge_idx = 9
    coedge_sense_idx = 10
    curve_idx = 11

    # Edge indices
    start_idx = 6
    stop_idx = 8
    point_idx = 7

    # Coedge row
    edge = sat_store.get(coedge.chunks[edge_idx])
    if "forward" in coedge.chunks[coedge_sense_idx]:
        ori = True
    else:
        ori = False

    vertex1 = sat_store.get(edge.chunks[start_idx])
    vertex2 = sat_store.get(edge.chunks[stop_idx])
    p1 = Point(*[float(x) for x in sat_store.get(vertex1.chunks[point_idx]).chunks[6:9]])
    p2 = Point(*[float(x) for x in sat_store.get(vertex2.chunks[point_idx]).chunks[6:9]])

    curve_record = sat_store.get(edge.chunks[curve_idx])
    if curve_record.type == "straight-curve" or Config().sat_read_curve_ignore_bspline:
        edge_element = geo_cu.Edge(p1, p2)
    elif curve_record.type == "intcurve-curve":
        edge_curve = create_bspline_curve_from_sat(curve_record)
        edge_element = geo_cu.EdgeCurve(p1, p2, edge_curve, True)
    else:
        raise NotImplementedError(f"Curve type {curve_record.type} is not supported.")

    return geo_cu.OrientedEdge(p1, p2, edge_element, ori)


def iter_loop_coedges(loop_record: AcisRecord) -> Iterable[geo_cu.OrientedEdge]:
    """Iterates over the edges of the face."""
    sat_store = loop_record.sat_store
    # Coedge indices
    coedge_ref = 7
    direction_idx = -4
    coedge_start_id = loop_record.chunks[coedge_ref]
    coedge_first = sat_store.get(coedge_start_id)
    coedge_first_direction = str(coedge_first.chunks[direction_idx])
    next_coedge_idx = 6 if coedge_first_direction == "forward" else 7
    coedge_next_id = coedge_first.chunks[next_coedge_idx]

    yield get_edge(coedge_first)

    max_iter = 100
    i = 0
    next_coedge = True
    while next_coedge is True:
        coedge = sat_store.get(coedge_next_id)

        yield get_edge(coedge)

        coedge_next_id = coedge.chunks[next_coedge_idx]
        if coedge_next_id == coedge_start_id:
            next_coedge = False

        i += 1
        if i > max_iter:
            raise ValueError(f"Found {i} points which is over max={max_iter}")
