# Surface Types
from dataclasses import dataclass
from typing import Any

from ada.geom.placement import Axis2Placement3D


# STEP AP242 and IFC 4x3
@dataclass
class Plane:
    position: Axis2Placement3D


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcArbitraryProfileDefWithVoids.htm)
@dataclass
class ArbitraryProfileDefWithVoids:
    outer_curve: Any
    inner_curves: list[Any]


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcSurfaceOfLinearExtrusion.htm)
@dataclass
class SurfaceOfLinearExtrusion:
    swept_curve: Any
    position: Axis2Placement3D
    extrusion_direction: Any
    depth: float
