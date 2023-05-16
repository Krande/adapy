from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ada.geom.curves import CurveType
from ada.geom.placement import Axis2Placement3D


# STEP AP242 and IFC 4x3
@dataclass
class Plane:
    position: Axis2Placement3D


class ProfileType(Enum):
    AREA = "area"
    CURVE = "curve"


@dataclass
class ProfileDef:
    name: str
    profile_type: ProfileType


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcArbitraryProfileDefWithVoids.htm)
@dataclass
class ArbitraryProfileDefWithVoids(ProfileDef):
    outer_curve: CurveType
    inner_curves: list[Any] = field(default_factory=list)


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcSurfaceOfLinearExtrusion.htm)
@dataclass
class SurfaceOfLinearExtrusion:
    swept_curve: CurveType
    position: Axis2Placement3D
    extrusion_direction: CurveType
    depth: float


@dataclass
class SweptArea:
    area: ProfileType
    position: Axis2Placement3D
