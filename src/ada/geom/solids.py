from dataclasses import dataclass
from typing import Any

from ada.geom.placement import Axis2Placement3D, Direction
from ada.geom.points import Point

# Swept Solids: ExtrudedAreaSolid, RevolvedAreaSolid


@dataclass
class SweptArea:
    area: Any
    position: Axis2Placement3D


# STEP AP242 and IFC 4x3
@dataclass
class ExtrudedAreaSolid:
    swept_area: SweptArea
    extrusion_direction: Direction
    depth: float


# STEP AP242 and IFC 4x3
@dataclass
class RevolvedAreaSolid:
    swept_area: SweptArea
    axis: Direction
    angle: float


# STEP AP242 and IFC 4x3
@dataclass
class Box:
    position: Axis2Placement3D
    x_length: float
    y_length: float
    z_length: float


# STEP AP242
@dataclass
class Sphere:
    center: Point
    radius: float


# IFC 4x3
@dataclass
class CsgSolid:
    tree_root_expression: Any
