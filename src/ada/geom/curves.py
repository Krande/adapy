from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Union

from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point

CurveType = Union["Line", "ArcLine", "Circle", "Ellipse", "BSplineCurveWithKnots", "IndexedPolyCurve"]


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcLine.htm)
# STEP AP242
@dataclass
class Line:
    start: Point | Iterable
    end: Point | Iterable

    def __post_init__(self):
        if isinstance(self.start, Iterable):
            self.start = Point(*self.start)
        if isinstance(self.end, Iterable):
            self.end = Point(*self.end)

    @staticmethod
    def from_points(start: Iterable, end: Iterable):
        return Line(Point(*start), Point(*end))

    def __iter__(self):
        return iter((self.start, self.end))


@dataclass
class ArcLine:
    start: Point | Iterable
    center: Point | Iterable
    end: Point | Iterable

    def __post_init__(self):
        if isinstance(self.start, Iterable):
            self.start = Point(*self.start)
        if isinstance(self.center, Iterable):
            self.center = Point(*self.center)
        if isinstance(self.end, Iterable):
            self.end = Point(*self.end)

    @staticmethod
    def from_points(start: Iterable, center: Iterable, end: Iterable):
        return ArcLine(Point(*start), Point(*center), Point(*end))

    def __iter__(self):
        return iter((self.start, self.center, self.end))


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcCircle.htm)
# STEP AP242
@dataclass
class Circle:
    position: Axis2Placement3D
    radius: float


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcEllipse.htm)
# STEP AP242
@dataclass
class Ellipse:
    position: Axis2Placement3D
    semi_axis1: float
    semi_axis2: float


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcBSplineCurveForm.htm)
# STEP (https://www.steptools.com/stds/stp_aim/html/t_b_spline_curve_form.html)
class BSplineCurveFormEnum(Enum):
    POLYLINE_FORM = "POLYLINE_FORM"
    CIRCULAR_ARC = "CIRCULAR_ARC"
    ELLIPTIC_ARC = "ELLIPTIC_ARC"
    HYPERBOLIC_ARC = "HYPERBOLIC_ARC"
    PARABOLIC_ARC = "PARABOLIC_ARC"
    UNSPECIFIED = "UNSPECIFIED"


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcKnotType.htm)
# STEP (https://www.steptools.com/stds/stp_aim/html/t_knot_type.html)
class BsplineKnotSpecEnum(Enum):
    UNSPECIFIED = "UNSPECIFIED"
    PIECEWISE_BEZIER = "PIECEWISE_BEZIER"
    UNIFORM_KNOTS = "UNIFORM_KNOTS"
    QUASI_UNIFORM_KNOTS = "QUASI_UNIFORM_KNOTS"
    PIECEWISE_CUBIC = "PIECEWISE_CUBIC"


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcBSplineCurveWithKnots.htm)
# STEP (https://www.steptools.com/stds/stp_aim/html/t_b_spline_curve_with_knots.html)
@dataclass
class BSplineCurveWithKnots:
    degree: int
    control_points_list: list[Point] | list[tuple]
    curve_form: BSplineCurveFormEnum
    closed_curve: bool
    self_intersect: bool
    knot_multiplicities: list[int]
    knots: list[float]
    knot_spec: BsplineKnotSpecEnum


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcIndexedPolyCurve.htm)
# STEP (not found direct equivalent, but can be represented by using 'B_SPLINE_CURVE' and 'POLYLINE' entities)
@dataclass
class IndexedPolyCurve:
    segments: list[Line | ArcLine]
    self_intersect: bool = False
