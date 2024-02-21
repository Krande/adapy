from dataclasses import dataclass
from enum import Enum
from itertools import chain
from typing import Iterable, Union

import numpy as np

from ada.core.curve_utils import calc_arc_radius_center_from_3points
from ada.core.vector_utils import intersect_calc
from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point

CURVE_GEOM_TYPES = Union[
    "Line", "ArcLine", "Circle", "Ellipse", "BSplineCurveWithKnots", "IndexedPolyCurve", "PolyLine", "GeometricCurveSet"
]


@dataclass
class Line:
    """
    IFC4x3 https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcLine.htm
    (also) https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcLineIndex.htm
    STEP AP242 https://www.steptools.com/stds/stp_aim/html/t_line.html
    """

    start: Point | Iterable
    end: Point | Iterable

    def __post_init__(self):
        if isinstance(self.start, Iterable):
            self.start = Point(*self.start)
        if isinstance(self.end, Iterable):
            self.end = Point(*self.end)

        dim = self.start.dim
        if dim != self.end.dim:
            raise ValueError("Start and end points must have the same dimension")

    @staticmethod
    def from_points(start: Iterable, end: Iterable):
        return Line(Point(*start), Point(*end))

    @property
    def dim(self):
        return self.start.dim

    def __iter__(self):
        return iter((self.start, self.end))


@dataclass
class ArcLine:
    """
    IFC4x3 https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcArcIndex.htm

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
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcIndexedPolyCurve.htm)
    STEP (not found direct equivalent, but can be represented by using 'B_SPLINE_CURVE' and 'POLYLINE' entities)
    """

    segments: list[Line | ArcLine]
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

            if isinstance(seg, Line):
                if i == 0:
                    local_points.append(seg.start)
                else:
                    if type(segments[i - 1]) is Line:
                        local_points.append(seg.start)
                if i < len(segments) - 1:
                    if type(segments[i + 1]) is Line:
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
    IFC4x3 https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcCircle.htm
    STEP AP242 https://www.steptools.com/stds/stp_aim/html/t_circle.html
    """

    position: Axis2Placement3D
    radius: float


@dataclass
class Ellipse:
    """
    IFC4x3 https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcEllipse.htm
    STEP AP242 https://www.steptools.com/stds/stp_aim/html/t_ellipse.html
    """

    position: Axis2Placement3D
    semi_axis1: float
    semi_axis2: float


class BSplineCurveFormEnum(Enum):
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcBSplineCurveForm.htm)
    STEP (https://www.steptools.com/stds/stp_aim/html/t_b_spline_curve_form.html)
    """

    POLYLINE_FORM = "POLYLINE_FORM"
    CIRCULAR_ARC = "CIRCULAR_ARC"
    ELLIPTIC_ARC = "ELLIPTIC_ARC"
    HYPERBOLIC_ARC = "HYPERBOLIC_ARC"
    PARABOLIC_ARC = "PARABOLIC_ARC"
    UNSPECIFIED = "UNSPECIFIED"


class BsplineKnotSpecEnum(Enum):
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcKnotType.htm)
    STEP (https://www.steptools.com/stds/stp_aim/html/t_knot_type.html)
    """

    UNSPECIFIED = "UNSPECIFIED"
    PIECEWISE_BEZIER = "PIECEWISE_BEZIER"
    UNIFORM_KNOTS = "UNIFORM_KNOTS"
    QUASI_UNIFORM_KNOTS = "QUASI_UNIFORM_KNOTS"
    PIECEWISE_CUBIC = "PIECEWISE_CUBIC"


@dataclass
class BSplineCurveWithKnots:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcBSplineCurveWithKnots.htm)
    STEP (https://www.steptools.com/stds/stp_aim/html/t_b_spline_curve_with_knots.html)
    """

    degree: int
    control_points_list: list[Point] | list[tuple]
    curve_form: BSplineCurveFormEnum
    closed_curve: bool
    self_intersect: bool
    knot_multiplicities: list[int]
    knots: list[float]
    knot_spec: BsplineKnotSpecEnum
