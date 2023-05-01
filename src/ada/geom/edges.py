from enum import Enum

from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point

from dataclasses import dataclass


# Curve Types
# STEP AP242 and IFC 4x3
@dataclass
class Line:
    start: Point
    end: Point


# STEP AP242 and IFC 4x3
@dataclass
class Circle:
    position: Axis2Placement3D
    radius: float


# STEP AP242 and IFC 4x3
@dataclass
class Ellipse:
    position: Axis2Placement3D
    semi_axis1: float
    semi_axis2: float


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcBSplineCurveForm.htm)
# STEP (https://www.steptools.com/stds/stp_aim/html/t_b_spline_curve_form.html)
class CurveFormEnum(Enum):
    POLYLINE_FORM = "POLYLINE_FORM"
    CIRCULAR_ARC = "CIRCULAR_ARC"
    ELLIPTIC_ARC = "ELLIPTIC_ARC"
    HYPERBOLIC_ARC = "HYPERBOLIC_ARC"
    PARABOLIC_ARC = "PARABOLIC_ARC"
    UNSPECIFIED = "UNSPECIFIED"


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcBSplineCurveWithKnots.htm)
# STEP (https://www.steptools.com/stds/stp_aim/html/t_b_spline_curve_with_knots.html)
@dataclass
class BSplineCurveWithKnots:
    degree: int
    control_points_list: list[Point]
    curve_form: CurveFormEnum
    closed_curve: bool
    self_intersect: bool
    knot_multiplicities: list[int]
    knots: list[float]
    knot_spec: str


@dataclass
class IndexedPolyCurve:
    points: list[Point]
    segments: list[int]
    self_intersect: bool = False
