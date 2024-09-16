from __future__ import annotations

from typing import Iterable

from ada import Point
import numpy as np

from ada.cadit.sat.read.bsplinecurves import create_bspline_curve_from_sat
from ada.cadit.sat.read.bsplinesurface import ACISReferenceDataError
from ada.cadit.sat.read.sat_entities import AcisRecord
from ada.config import Config
from ada.geom import curves as geo_cu
from ada.geom.placement import Axis2Placement3D, Direction


class UnsupportedCurveType(Exception):
    pass


def get_ellipse_curve(ellipse_record: AcisRecord) -> geo_cu.Ellipse:
    chunks = ellipse_record.chunks

    # Ellipse indices
    center_idx = 6
    major_axis_idx = 12
    minor_axis_idx = 9

    # Extract the center point
    center = Point(
        float(chunks[center_idx]),
        float(chunks[center_idx + 1]),
        float(chunks[center_idx + 2])
    )

    # Extract major and minor axis vectors
    major_axis_vector = Direction(
        float(chunks[major_axis_idx]),
        float(chunks[major_axis_idx + 1]),
        float(chunks[major_axis_idx + 2])
    )
    minor_axis_vector = Direction(
        float(chunks[minor_axis_idx]),
        float(chunks[minor_axis_idx + 1]),
        float(chunks[minor_axis_idx + 2])
    )

    # Compute semi-axis lengths
    semi_axis1 = major_axis_vector.get_length()
    semi_axis2 = minor_axis_vector.get_length()

    # Normalize the major axis vector
    direction_major_axis = major_axis_vector.get_normalized()

    # Compute the normal vector using cross product
    normal_vector = Direction(np.cross(major_axis_vector, minor_axis_vector)).get_normalized()

    # Create the position object
    position = Axis2Placement3D(
        location=center,
        axis=normal_vector,
        ref_direction=direction_major_axis
    )

    return geo_cu.Ellipse(position, semi_axis1=semi_axis1, semi_axis2=semi_axis2)


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
        if edge_curve is None:
            raise ACISReferenceDataError("Failed to create B-spline curve from SAT data")
        edge_element = geo_cu.EdgeCurve(p1, p2, edge_curve, True)
    elif curve_record.type == "ellipse-curve":
        edge_curve = get_ellipse_curve(curve_record)
        edge_element = geo_cu.EdgeCurve(p1, p2, edge_curve, True)
    else:
        raise UnsupportedCurveType(f"Curve type {curve_record.type} is not supported.")

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
