from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from itertools import chain
from typing import TYPE_CHECKING, Iterable, Union

import numpy as np

from ada.core.curve_utils import calc_arc_radius_center_from_3points
from ada.core.vector_utils import intersect_calc
from ada.geom.placement import Axis2Placement3D, Direction
from ada.geom.points import Point

if TYPE_CHECKING:
    from ada.geom.surfaces import SURFACE_GEOM_TYPES

CURVE_GEOM_TYPES = Union[
    "Line",
    "ArcLine",
    "Circle",
    "Ellipse",
    "BSplineCurveWithKnots",
    "IndexedPolyCurve",
    "PolyLine",
    "GeometricCurveSet",
]


@dataclass
class Line:
    """
    IFC4x3 https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcLine.htm
    STEP AP242 https://www.steptools.com/stds/stp_aim/html/t_line.html
    """

    pnt: Point | Iterable
    dir: Direction | Iterable

    def __post_init__(self):
        if isinstance(self.pnt, Iterable):
            self.pnt = Point(*self.pnt)
        if isinstance(self.dir, Iterable):
            self.dir = Direction(*self.dir)


@dataclass
class ArcLine:
    """
    IFC4x3 https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcArcIndex.htm

    """

    start: Point | Iterable
    midpoint: Point | Iterable
    end: Point | Iterable

    def __post_init__(self):
        if isinstance(self.start, Iterable):
            self.start = Point(*self.start)
        if isinstance(self.midpoint, Iterable):
            self.midpoint = Point(*self.midpoint)
        if isinstance(self.end, Iterable):
            self.end = Point(*self.end)

        dim = self.start.dim
        if dim != self.end.dim or dim != self.midpoint.dim:
            raise ValueError("Start and end points must have the same dimension")

    @property
    def dim(self):
        return self.start.dim

    def __iter__(self):
        return iter((self.start, self.midpoint, self.end))


@dataclass
class PolyLine:
    points: list[Point]


@dataclass
class IndexedPolyCurve:
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcIndexedPolyCurve.htm)
    STEP (not found direct equivalent, but can be represented by using 'B_SPLINE_CURVE' and 'POLYLINE' entities)
    """

    segments: list[Edge | ArcLine]
    self_intersect: bool = False

    def get_points_and_segment_indices(self) -> tuple[np.ndarray, list[list[int]]]:
        points = list(chain.from_iterable([list(segment) for segment in self.segments]))
        points_tuple = [tuple(x) for x in chain.from_iterable([list(segment) for segment in self.segments])]
        unique_pts, pts_index = np.unique(points, axis=0, return_index=False, return_inverse=True)
        indices = [[int(pts_index[points_tuple.index(tuple(s))]) + 1 for s in segment] for segment in self.segments]

        return unique_pts, indices

    def to_points2d(self):
        local_points = []
        segments_in = self.segments
        segments = segments_in[1:]
        for i, seg in enumerate(segments):
            if i == 0:
                pseg = segments[-1]
            else:
                pseg = segments[i - 1]

            if i == len(segments) - 1:
                nseg = segments[0]
            else:
                nseg = segments[i + 1]

            if isinstance(seg, Edge):
                if i == 0:
                    local_points.append(seg.start)
                else:
                    if type(segments[i - 1]) is Edge:
                        local_points.append(seg.start)
                if i < len(segments) - 1:
                    if type(segments[i + 1]) is Edge:
                        local_points.append(seg.end)
                else:
                    local_points.append(seg.end)
            else:
                center, radius = calc_arc_radius_center_from_3points(seg.start, seg.midpoint, seg.end)
                v1_ = seg.start - pseg.start
                v2_ = nseg.end - seg.end
                # ed = np.cross(v1_, v2_)
                # if ed < 0:
                #     local_points.append(seg.start)

                s, t = intersect_calc(seg.start, nseg.end, v1_, v2_)
                ip = seg.start + s * v1_
                # ip = intersection_point(v1_, v2_)
                local_points.append((ip[0], ip[1], radius))

        return local_points


@dataclass
class GeometricCurveSet:
    elements: list[CURVE_GEOM_TYPES]


@dataclass
class Circle:
    """
    IFC4x3 https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcCircle.htm
    STEP AP242 https://www.steptools.com/stds/stp_aim/html/t_circle.html
    """

    position: Axis2Placement3D
    radius: float


@dataclass
class Ellipse:
    """
    IFC4x3 https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcEllipse.htm
    STEP AP242 https://www.steptools.com/stds/stp_aim/html/t_ellipse.html
    """

    position: Axis2Placement3D
    semi_axis1: float
    semi_axis2: float


class BSplineCurveFormEnum(Enum):
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcBSplineCurveForm.htm)
    STEP (https://www.steptools.com/stds/stp_aim/html/t_b_spline_curve_form.html)
    """

    POLYLINE_FORM = "POLYLINE_FORM"
    CIRCULAR_ARC = "CIRCULAR_ARC"
    ELLIPTIC_ARC = "ELLIPTIC_ARC"
    HYPERBOLIC_ARC = "HYPERBOLIC_ARC"
    PARABOLIC_ARC = "PARABOLIC_ARC"
    UNSPECIFIED = "UNSPECIFIED"


class KnotType(Enum):
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcKnotType.htm)
    STEP (https://www.steptools.com/stds/stp_aim/html/t_knot_type.html)
    """

    PIECEWISE_BEZIER_KNOTS = "PIECEWISE_BEZIER_KNOTS"
    QUASI_UNIFORM_KNOTS = "QUASI_UNIFORM_KNOTS"
    UNIFORM_KNOTS = "UNIFORM_KNOTS"
    UNSPECIFIED = "UNSPECIFIED"

    @staticmethod
    def from_str(value: str) -> KnotType:
        return KnotType(value)


@dataclass
class BSplineCurveWithKnots:
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcBSplineCurveWithKnots.htm)
    STEP (https://www.steptools.com/stds/stp_aim/html/t_b_spline_curve_with_knots.html)
    """

    degree: int
    control_points_list: list[Point] | list[tuple]
    curve_form: BSplineCurveFormEnum
    closed_curve: bool
    self_intersect: bool
    knot_multiplicities: list[int]
    knots: list[float]
    knot_spec: KnotType


@dataclass
class PCurve:
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcPcurve.htm)
    """

    basis_surface: SURFACE_GEOM_TYPES
    reference_curve: CURVE_GEOM_TYPES


@dataclass
class RationalBSplineCurveWithKnots(BSplineCurveWithKnots):
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcRationalBSplineCurveWithKnots.htm)
    """

    weights_data: list[float]


@dataclass
class Edge:
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcEdge.htm)
    STEP (https://www.steptools.com/stds/stp_aim/html/t_edge.html)
    """

    start: Point
    end: Point

    def __post_init__(self):
        if isinstance(self.start, Iterable):
            self.start = Point(*self.start)
        if isinstance(self.end, Iterable):
            self.end = Point(*self.end)

        dim = self.start.dim
        if dim != self.end.dim:
            raise ValueError("Start and end points must have the same dimension")

    def reversed(self):
        return Edge(self.end, self.start)

    @property
    def dim(self):
        return self.start.dim

    def __iter__(self):
        return iter((self.start, self.end))


@dataclass
class OrientedEdge(Edge):
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcOrientedEdge.htm)
    STEP (https://www.steptools.com/stds/stp_aim/html/t_oriented_edge.html)
    """

    edge_element: Edge | EdgeCurve
    orientation: bool


@dataclass
class EdgeCurve(Edge):
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcEdgeCurve.htm)
    STEP (https://www.steptools.com/stds/stp_aim/html/t_edge_curve.html)
    """

    edge_geometry: CURVE_GEOM_TYPES
    same_sense: bool


@dataclass
class PolyLoop:
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcPolyLoop.htm)
    """

    polygon: list[Point]


@dataclass
class EdgeLoop:
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcEdgeLoop.htm)
    """

    edge_list: list[OrientedEdge]
