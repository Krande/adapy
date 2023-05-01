# Surface Types
from dataclasses import dataclass

from ada.geom.placement import Axis2Placement3D


# STEP AP242 and IFC 4x3
@dataclass
class Plane:
    position: Axis2Placement3D


# STEP AP242 and IFC 4x3
@dataclass
class Cylinder:
    position: Axis2Placement3D
    radius: float


@dataclass
class ArbitraryProfileDefWithVoids:
    outer_curve: Any
    inner_curves: list[Any]
