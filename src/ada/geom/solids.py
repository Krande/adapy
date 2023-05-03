from dataclasses import dataclass
from typing import Any

from ada.geom.placement import Axis2Placement3D, Direction
from ada.geom.points import Point


# Swept Solids: ExtrudedAreaSolid, RevolvedAreaSolid
@dataclass
class SweptArea:
    area: Any
    position: Axis2Placement3D


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcExtrudedAreaSolid.htm)
# STEP AP242
@dataclass
class ExtrudedAreaSolid:
    swept_area: SweptArea
    extrusion_direction: Direction
    depth: float


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcRevolvedAreaSolid.htm)
# STEP AP242
@dataclass
class RevolvedAreaSolid:
    swept_area: SweptArea
    axis: Direction
    angle: float


# CSG solids


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcBlock.htm)
# STEP AP242 and IFC 4x3
@dataclass
class Box:
    position: Axis2Placement3D
    x_length: float
    y_length: float
    z_length: float


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcRectangularPyramid.htm)
@dataclass
class RectangularPyramid:
    position: Axis2Placement3D
    x_length: float
    y_length: float
    z_length: float


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcRightCircularCone.htm)
@dataclass
class Cone:
    position: Axis2Placement3D
    bottom_radius: float
    height: float


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcRightCircularCylinder.htm)
@dataclass
class Cylinder:
    position: Axis2Placement3D
    radius: float
    height: float


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcSphere.htm)
# STEP AP242
@dataclass
class Sphere:
    center: Point
    radius: float
