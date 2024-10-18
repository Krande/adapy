from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Union

import ada.geom.curves as geo_cu
from ada.geom.curves import EdgeLoop, PolyLoop
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
class FaceBound:
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcFaceBound.htm)
    """

    bound: Union[PolyLoop, EdgeLoop]
    orientation: bool


@dataclass
class Face:
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcFace.htm)
    """

    bounds: list[FaceBound]


@dataclass
class FaceSurface(Face):
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcFaceSurface.htm)
    """

    face_surface: Union[Plane]
    same_sense: bool = True


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
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcCurveBoundedPlane.htm)
    """

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


@dataclass
class CircleProfileDef(ProfileDef):
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcCircleProfileDef.htm)
    """

    radius: float


@dataclass
class TriangulatedFaceSet:
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcTriangulatedFaceSet.htm)
    """

    coordinates: list[Point]
    normals: list[Direction]
    indices: list[int]


@dataclass
class RectangleProfileDef(ProfileDef):
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcRectangleProfileDef.htm)
    """

    x_dim: float
    y_dim: float


class BSplineSurfaceForm(Enum):
    PLANE_SURF = "PLANE_SURF"
    CYLINDRICAL_SURF = "CYLINDRICAL_SURF"
    CONICAL_SURF = "CONICAL_SURF"
    SPHERICAL_SURF = "SPHERICAL_SURF"
    TOROIDAL_SURF = "TOROIDAL_SURF"
    SURF_OF_REVOLUTION = "SURF_OF_REVOLUTION"
    RULED_SURF = "RULED_SURF"
    GENERALISED_CONE = "GENERALISED_CONE"
    QUADRIC_SURF = "QUADRIC_SURF"
    SURF_OF_LINEAR_EXTRUSION = "SURF_OF_LINEAR_EXTRUSION"
    UNSPECIFIED = "UNSPECIFIED"

    @staticmethod
    def from_str(value: str) -> BSplineSurfaceForm:
        return BSplineSurfaceForm(value)


@dataclass
class BSplineSurface:
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcBSplineSurface.htm)
    """

    u_degree: int
    v_degree: int
    control_points_list: list[list[Point]]
    surface_form: BSplineSurfaceForm
    u_closed: bool
    v_closed: bool
    self_intersect: bool


@dataclass
class BSplineSurfaceWithKnots(BSplineSurface):
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcBSplineSurfaceWithKnots.htm)
    """

    u_multiplicities: list[int]
    v_multiplicities: list[int]
    u_knots: list[float]
    v_knots: list[float]
    knot_spec: geo_cu.KnotType

    def get_num_u_control_points(self) -> int:
        return len(self.control_points_list)

    def get_num_v_control_points(self) -> int:
        return len(self.control_points_list[0])

    def to_json(self):
        return {
            "u_degree": self.u_degree,
            "v_degree": self.v_degree,
            "control_points_list": [[(p.x, p.y, p.z) for p in row] for row in self.control_points_list],
            "surface_form": self.surface_form.value,
            "u_closed": self.u_closed,
            "v_closed": self.v_closed,
            "self_intersect": self.self_intersect,
            "u_multiplicities": self.u_multiplicities,
            "v_multiplicities": self.v_multiplicities,
            "u_knots": self.u_knots,
            "v_knots": self.v_knots,
            "knot_spec": self.knot_spec.value,
        }


@dataclass
class RationalBSplineSurfaceWithKnots(BSplineSurfaceWithKnots):
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcRationalBSplineSurfaceWithKnots.htm)
    """

    weights_data: list[list[float]]


@dataclass
class ClosedShell:
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcClosedShell.htm)
    """

    cfs_faces: list[Face | FaceSurface | Plane]


@dataclass
class AdvancedFace:
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcAdvancedFace.htm)
    """

    bounds: list[FaceBound]
    face_surface: Union[
        ArbitraryProfileDef,
        CircleProfileDef,
        RectangleProfileDef,
        BSplineSurface,
        BSplineSurfaceWithKnots,
        RationalBSplineSurfaceWithKnots,
    ]
    same_sense: bool = True


SURFACE_GEOM_TYPES = Union[
    ArbitraryProfileDef,
    FaceBasedSurfaceModel,
    CurveBoundedPlane,
    IShapeProfileDef,
    TShapeProfileDef,
    TriangulatedFaceSet,
    CircleProfileDef,
    RectangleProfileDef,
    AdvancedFace,
    BSplineSurfaceWithKnots,
    RationalBSplineSurfaceWithKnots,
    SurfaceOfLinearExtrusion,
    ClosedShell,
]
