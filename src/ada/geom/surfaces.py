from dataclasses import dataclass, field
from enum import Enum
from typing import Union

import ada.geom.curves as geo_cu
from ada.geom.placement import Axis2Placement3D, Direction
from ada.geom.points import Point


# STEP AP242 and IFC 4x3
@dataclass
class Plane:
    position: Axis2Placement3D


class ProfileType(Enum):
    AREA = "area"
    CURVE = "curve"

    @staticmethod
    def from_str(profile_type: str) -> "ProfileType":
        if profile_type.upper() == "AREA":
            return ProfileType.AREA
        elif profile_type.upper() == "CURVE":
            return ProfileType.CURVE
        else:
            raise ValueError(f"Invalid profile type {profile_type}")


@dataclass
class ProfileDef:
    profile_type: ProfileType


@dataclass
class ArbitraryProfileDef(ProfileDef):
    """
    IFC4x3 https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcArbitraryProfileDefWithVoids.htm
    """

    outer_curve: geo_cu.CURVE_GEOM_TYPES
    inner_curves: list[geo_cu.CURVE_GEOM_TYPES] = field(default_factory=list)
    profile_name: str = None

    def __post_init__(self):
        # Check consistency of dimensions
        if isinstance(self.outer_curve, geo_cu.IndexedPolyCurve):
            for segment in self.outer_curve.segments:
                if segment.dim != 2:
                    raise ValueError("Invalid segment in outer_curve")


@dataclass
class PolyLoop:
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcPolyLoop.htm)
    """

    polygon: list[Point]


@dataclass
class FaceBound:
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcFaceBound.htm)
    """

    bound: PolyLoop
    orientation: bool


@dataclass
class ConnectedFaceSet:
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcConnectedFaceSet.htm)
    """

    cfs_faces: list[FaceBound]


@dataclass
class FaceBasedSurfaceModel:
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcFaceBasedSurfaceModel.htm)
    """

    fbsm_faces: list[ConnectedFaceSet]


@dataclass
class CurveBoundedPlane:
    basis_surface: Plane
    outer_boundary: geo_cu.CURVE_GEOM_TYPES
    inner_boundaries: list[geo_cu.CURVE_GEOM_TYPES] = field(default_factory=list)


@dataclass
class SurfaceOfLinearExtrusion:
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcSurfaceOfLinearExtrusion.htm)
    """

    swept_curve: geo_cu.CURVE_GEOM_TYPES
    position: Axis2Placement3D
    extrusion_direction: Direction
    depth: float


@dataclass
class IShapeProfileDef(ProfileDef):
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcIShapeProfileDef.htm)
    """

    overall_width: float
    overall_depth: float
    web_thickness: float
    flange_thickness: float
    fillet_radius: float
    flange_edge_radius: float
    flange_slope: float


@dataclass
class TShapeProfileDef(ProfileDef):
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcTShapeProfileDef.htm)
    """

    depth: float
    flange_width: float
    web_thickness: float
    flange_thickness: float
    fillet_radius: float
    flange_edge_radius: float
    web_edge_radius: float
    web_slope: float
    flange_slope: float


SURFACE_GEOM_TYPES = Union[
    ArbitraryProfileDef, FaceBasedSurfaceModel, CurveBoundedPlane, IShapeProfileDef, TShapeProfileDef
]
