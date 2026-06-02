from __future__ import annotations

from typing import Iterable

from ada import Direction, Point
from ada.cadit.sat.exceptions import ACISReferenceDataError, ACISUnsupportedCurveType
from ada.cadit.sat.read.bsplinecurves import (
    create_bspline_curve_from_sat,
    create_pcurve_2d_from_sat_record,
)
from ada.cadit.sat.read.sat_entities import AcisRecord
from ada.config import Config, logger
from ada.geom import curves as geo_cu
from ada.geom.placement import Axis2Placement3D


def _reverse_pcurve_2d(pc: geo_cu.Pcurve2dBSpline) -> geo_cu.Pcurve2dBSpline:
    """Return a reversed copy of a 2D B-spline pcurve.

    Reversal: reverse the control-point sequence (and weights, if
    rational), then reflect the knot vector around its midpoint
    (``new_knots[i] = first + last - knots[n-1-i]``) with multiplicities
    in the same reversed order. This preserves curve shape while
    flipping its parameterization direction — the right transform for
    bringing an ACIS pcurve into alignment with an OCC edge that
    traverses the 3D curve in the opposite direction.
    """
    knots = list(pc.knots)
    mults = list(pc.knot_multiplicities)
    if not knots:
        return pc
    a = knots[0]
    b = knots[-1]
    new_knots = [a + b - k for k in reversed(knots)]
    new_mults = list(reversed(mults))
    new_cps = list(reversed(pc.control_points_2d))
    new_weights = list(reversed(pc.weights)) if pc.weights else None
    return geo_cu.Pcurve2dBSpline(
        degree=pc.degree,
        control_points_2d=new_cps,
        knots=new_knots,
        knot_multiplicities=new_mults,
        weights=new_weights,
        closed=pc.closed,
    )


def get_ellipse_curve(ellipse_record: AcisRecord) -> geo_cu.Ellipse | geo_cu.Circle:
    chunks = ellipse_record.chunks

    # Ellipse indices
    center_idx = 6
    major_axis_idx = 12
    normal_vector_idx = 9

    # Extract the center point
    center = Point(float(chunks[center_idx]), float(chunks[center_idx + 1]), float(chunks[center_idx + 2]))
    normal_vector = Direction(
        float(chunks[normal_vector_idx]), float(chunks[normal_vector_idx + 1]), float(chunks[normal_vector_idx + 2])
    )

    # Extract major and minor axis vectors
    major_axis_vector = Direction(
        float(chunks[major_axis_idx]), float(chunks[major_axis_idx + 1]), float(chunks[major_axis_idx + 2])
    )

    # Compute semi-axis lengths
    semi_axis1 = major_axis_vector.get_length()
    semi_axis2 = normal_vector.get_length()

    # Normalize the major axis vector
    direction_major_axis = major_axis_vector.get_normalized()

    # Compute the normal vector using cross product
    normal_vector = normal_vector.get_normalized()

    # Create the position object
    position = Axis2Placement3D(location=center, axis=normal_vector, ref_direction=direction_major_axis)
    if semi_axis2 == 1.0:
        return geo_cu.Circle(position, semi_axis1)

    return geo_cu.Ellipse(position, semi_axis1=semi_axis1, semi_axis2=semi_axis2)


def create_line_from_sat(line_record: AcisRecord) -> geo_cu.Line:
    chunks = line_record.chunks

    # Line indices
    start_idx = 6
    direction_idx = 9

    # Extract the start and stop points
    start_point = Point(float(chunks[start_idx]), float(chunks[start_idx + 1]), float(chunks[start_idx + 2]))
    direction = Direction(
        float(chunks[direction_idx]), float(chunks[direction_idx + 1]), float(chunks[direction_idx + 2])
    )

    return geo_cu.Line(start_point, direction)


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

    edge = sat_store.get(coedge.chunks[edge_idx])
    curve_record = sat_store.get(edge.chunks[curve_idx])
    edge_direction = edge.chunks[12]
    coedge_direction = coedge.chunks[coedge_sense_idx]
    coedge_dir_bool = coedge_direction == "forward"

    vertex1 = sat_store.get(edge.chunks[start_idx])
    vertex2 = sat_store.get(edge.chunks[stop_idx])
    p1 = Point(*[float(x) for x in sat_store.get(vertex1.chunks[point_idx]).chunks[6:9]])
    p2 = Point(*[float(x) for x in sat_store.get(vertex2.chunks[point_idx]).chunks[6:9]])
    if not coedge_dir_bool:
        p1, p2 = p2, p1

    curve_direction = "forward"
    if curve_record.type == "straight-curve" or Config().sat_read_curve_ignore_bspline:
        line_element = create_line_from_sat(curve_record)
        edge_element = geo_cu.EdgeCurve(p1, p2, line_element, coedge_dir_bool)
    elif curve_record.type == "intcurve-curve":
        edge_curve = create_bspline_curve_from_sat(curve_record)
        curve_direction = curve_record.chunks[6]
        if edge_curve is None:
            raise ACISReferenceDataError("Failed to create B-spline curve from SAT data")
        edge_element = geo_cu.EdgeCurve(p1, p2, edge_curve, coedge_dir_bool)
    elif curve_record.type == "ellipse-curve":
        edge_curve = get_ellipse_curve(curve_record)
        edge_element = geo_cu.EdgeCurve(p1, p2, edge_curve, coedge_dir_bool)
    else:
        raise ACISUnsupportedCurveType(f"Curve type {curve_record.type} is not supported.")

    for direction in [edge_direction, coedge_direction, curve_direction]:
        if direction not in ["forward", "reversed"]:
            raise ValueError(f"Invalid direction: {direction}")

    # calculate the orientation of the edge
    edge_dir_value = 1 if edge_direction == "forward" else -1

    if edge_dir_value == 1:
        ori = True
    else:
        ori = False

    # Resolve the per-coedge UV-space p-curve when the SAT file provides
    # one. The pcurve ref sits in coedge.chunks[12] (after edge=9, sense=10,
    # loop=11). Carrying it forward lets the OCCT face-builder skip the
    # lossy reproject-and-fit fallback in update_edges_uv_gen.
    #
    # Sense composition: the OCC edge runs along the underlying 3D
    # intcurve iff edge.sense == coedge.sense (each ``reversed`` flips
    # the direction). The pcurve as authored runs along the intcurve
    # when its own sense=forward. Reverse the 2D curve at parse time
    # whenever the two disagree, so the consumer can attach as-is.
    pcurve_geom: geo_cu.Pcurve2dBSpline | None = None
    chunks = coedge.chunks
    if len(chunks) > 12:
        pcurve_chunk = chunks[12]
        if pcurve_chunk and pcurve_chunk.startswith("$") and pcurve_chunk != "$-1":
            try:
                pcurve_record = sat_store.get(pcurve_chunk)
            except Exception:
                pcurve_record = None
            if pcurve_record is not None and getattr(pcurve_record, "type", None) == "pcurve":
                try:
                    pcurve_geom = create_pcurve_2d_from_sat_record(pcurve_record)
                    if pcurve_geom is not None:
                        # Pcurve sense: chunks[7] is forward/reversed.
                        # Reverse strategy is tunable via env var while
                        # we sort out the right composition; "auto" uses
                        # the conservative rule "reverse iff pcurve sense
                        # disagrees with (edge.sense XOR coedge.sense)".
                        # Set ADA_PCURVE_REVERSE=never|always|auto|pcurve_only.
                        import os as _os

                        mode = (_os.environ.get("ADA_PCURVE_REVERSE") or "auto").strip().lower()
                        pcurve_sense = "forward"
                        if len(pcurve_record.chunks) > 7 and pcurve_record.chunks[7] in ("forward", "reversed"):
                            pcurve_sense = pcurve_record.chunks[7]
                        do_reverse = False
                        if mode == "never":
                            do_reverse = False
                        elif mode == "always":
                            do_reverse = True
                        elif mode == "pcurve_only":
                            do_reverse = pcurve_sense == "reversed"
                        else:  # auto
                            occ_edge_along_intcurve = edge_direction == coedge_direction
                            pcurve_along_intcurve = pcurve_sense == "forward"
                            do_reverse = occ_edge_along_intcurve != pcurve_along_intcurve
                        if do_reverse:
                            pcurve_geom = _reverse_pcurve_2d(pcurve_geom)
                except Exception as ex:
                    logger.debug("pcurve %s not usable: %s", pcurve_chunk, ex)
                    pcurve_geom = None

    # SAT edge stores the underlying curve's parameters at the start
    # and end vertices (chunks[7]=t1, chunks[9]=t2). Carrying them
    # forward lets the OCC builder use parameter-trim instead of 3D-
    # point trim — which is critical for closed curves like circles
    # where two arcs connect the same two points (the point-trim
    # overload picks one of them and gets it wrong on the long-arc
    # half of the time).
    t_start: float | None = None
    t_end: float | None = None
    try:
        t_start = float(edge.chunks[7])
        t_end = float(edge.chunks[9])
    except (ValueError, IndexError, TypeError):
        pass
    # Coedge orientation: when the coedge runs the edge backwards we
    # already swapped p1/p2 above — also swap t_start/t_end so the OCC
    # edge runs from p1 (at t_start) to p2 (at t_end) along the
    # curve's natural direction.
    if not coedge_dir_bool and t_start is not None and t_end is not None:
        t_start, t_end = t_end, t_start

    return geo_cu.OrientedEdge(
        p1,
        p2,
        edge_element,
        ori,
        pcurve=pcurve_geom,
        t_start=t_start,
        t_end=t_end,
    )


def iter_loop_coedges(loop_record: AcisRecord) -> Iterable[geo_cu.OrientedEdge]:
    """Iterates over the edges of the face."""
    sat_store = loop_record.sat_store
    # Coedge indices
    coedge_ref = 7
    coedge_start_id = loop_record.chunks[coedge_ref]
    coedge_first = sat_store.get(coedge_start_id)
    next_coedge_idx = 6  # if coedge_first_direction == "forward" else 7
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
