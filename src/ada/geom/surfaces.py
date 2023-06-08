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


@dataclass
class ProfileDef:
    profile_type: ProfileType


@dataclass
class ArbitraryProfileDefWithVoids(ProfileDef):
    """
    IFC4x3 https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcArbitraryProfileDefWithVoids.htm
    """

    outer_curve: geo_cu.CURVE_GEOM_TYPES
    inner_curves: list[geo_cu.CURVE_GEOM_TYPES] = field(default_factory=list)

    def __post_init__(self):
        # Check consistency of dimensions
        if isinstance(self.outer_curve, geo_cu.IndexedPolyCurve):
            for segment in self.outer_curve.segments:
                if segment.dim != 2:
                    raise ValueError("Invalid segment in outer_curve")


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcPolyLoop.htm)
@dataclass
class PolyLoop:
    polygon: list[Point]


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcFaceBound.htm)
@dataclass
class FaceBound:
    bound: PolyLoop
    orientation: bool


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcConnectedFaceSet.htm)
@dataclass
class ConnectedFaceSet:
    cfs_faces: list[FaceBound]


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcFaceBasedSurfaceModel.htm)
@dataclass
class FaceBasedSurfaceModel:
    fbsm_faces: list[ConnectedFaceSet]


@dataclass
class CurveBoundedPlane:
    basis_surface: Plane
    outer_boundary: geo_cu.CURVE_GEOM_TYPES
    inner_boundaries: list[geo_cu.CURVE_GEOM_TYPES] = field(default_factory=list)


# IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcSurfaceOfLinearExtrusion.htm)


@dataclass
class SurfaceOfLinearExtrusion:
    swept_curve: geo_cu.CURVE_GEOM_TYPES
    position: Axis2Placement3D
    extrusion_direction: Direction
    depth: float


SURFACE_GEOM_TYPES = Union[ArbitraryProfileDefWithVoids, FaceBasedSurfaceModel, CurveBoundedPlane]
