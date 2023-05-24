from __future__ import annotations
from dataclasses import dataclass, field
from typing import Iterable

from ada.geom.placement import Axis1Placement, Axis2Placement3D, Direction
from ada.geom.points import Point
from ada.geom.surfaces import ProfileDef


def _z_dir():
    return Direction(0, 0, 1)


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcExtrudedAreaSolid.htm)
# STEP AP242 (https://www.steptools.com/stds/stp_aim/html/t_extruded_area_solid.html)
@dataclass
class ExtrudedAreaSolid:
    swept_area: ProfileDef
    position: Axis2Placement3D
    depth: float
    extruded_direction: Direction | Iterable[float] = field(default_factory=_z_dir)

    def __post_init__(self):
        if isinstance(self.extruded_direction, Iterable):
            self.extruded_direction = Direction(*self.extruded_direction)


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcRevolvedAreaSolid.htm)
# STEP AP242 (https://www.steptools.com/stds/stp_aim/html/t_revolved_area_solid.html)
@dataclass
class RevolvedAreaSolid:
    swept_area: ProfileDef
    position: Axis2Placement3D
    axis: Axis1Placement
    angle: float


@dataclass
class FixedReferenceSweptAreaSolid:
    swept_area: ProfileDef
    position: Axis2Placement3D
    directrix: list[Point]


@dataclass
class DirectrixDerivedReferenceSweptAreaSolid(FixedReferenceSweptAreaSolid):
    pass


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcBlock.htm)
# STEP AP242 (https://www.steptools.com/stds/stp_aim/html/t_box_domain.html)
@dataclass
class Box:
    position: Axis2Placement3D
    x_length: float
    y_length: float
    z_length: float

    @staticmethod
    def from_xyz_and_dims(x, y, z, x_length: float, y_length: float, z_length: float, d1=None, d2=None) -> Box:
        d1 = d1 if d1 is not None else Direction(1, 0, 0)
        d2 = d2 if d2 is not None else Direction(0, 1, 0)
        axis3d = Axis2Placement3D(Point(x, y, z), d1, d2)
        return Box(axis3d, x_length, y_length, z_length)


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcRectangularPyramid.htm)
# STEP AP242 (https://www.steptools.com/stds/stp_aim/html/t_pyramid_volume.html)
@dataclass
class RectangularPyramid:
    position: Axis2Placement3D
    x_length: float
    y_length: float
    z_length: float


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcRightCircularCone.htm)
# STEP AP242 (https://www.steptools.com/stds/stp_aim/html/t_right_circular_cone.html)
@dataclass
class Cone:
    position: Axis2Placement3D
    bottom_radius: float
    height: float


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcRightCircularCylinder.htm)
# STEP AP242 (https://www.steptools.com/stds/stp_aim/html/t_right_circular_cylinder.html)
@dataclass
class Cylinder:
    position: Axis2Placement3D
    radius: float
    height: float


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcSphere.htm)
# STEP AP242 (https://www.steptools.com/stds/stp_aim/html/t_sphere.html)
@dataclass
class Sphere:
    center: Point
    radius: float
