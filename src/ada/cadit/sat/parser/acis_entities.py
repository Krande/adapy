"""
ACIS Entity Models

Pydantic models representing all ACIS geometry entities and their properties.
These models provide validation and type safety for ACIS data structures.
"""

from __future__ import annotations

from enum import Enum
from typing import Optional, Union, List
from pydantic import BaseModel, Field


class AcisVersion(BaseModel):
    """ACIS version information from SAT file header."""
    major: int
    minor: int
    point: int = 0

    @classmethod
    def from_string(cls, version_str: str) -> AcisVersion:
        """Parse version from string like '33.0.1'"""
        parts = version_str.split('.')
        return cls(
            major=int(parts[0]),
            minor=int(parts[1]) if len(parts) > 1 else 0,
            point=int(parts[2]) if len(parts) > 2 else 0
        )


class AcisHeader(BaseModel):
    """ACIS SAT file header information."""
    version_code: int
    num_records: int
    num_entities: int
    flags: int
    product_id: str = ""
    acis_version: Optional[AcisVersion] = None
    date: str = ""
    units_code: int = 1000
    resolution: float = 1e-6
    tolerance: float = 1e-10


class SenseType(str, Enum):
    """Direction/orientation sense."""
    FORWARD = "forward"
    REVERSED = "reversed"
    BOTH = "both"
    UNKNOWN = "unknown"


class ClosureType(str, Enum):
    """Spline closure type."""
    OPEN = "open"
    CLOSED = "closed"
    PERIODIC = "periodic"
    UNSET = "unset"


class NurbsType(str, Enum):
    """NURBS surface/curve type."""
    NURBS = "nurbs"  # Rational B-spline
    NUBS = "nubs"    # Non-rational B-spline
    NULLBS = "nullbs"  # Null B-spline


# Base ACIS Entity Classes

class AcisEntity(BaseModel):
    """Base class for all ACIS entities."""
    index: int = Field(..., description="Entity index in SAT file")
    entity_type: str = Field(..., description="ACIS entity type name")

    class Config:
        arbitrary_types_allowed = True


class AcisGeometricEntity(AcisEntity):
    """Base class for geometric entities."""
    pass


# Topological Entities

class AcisBody(AcisEntity):
    """ACIS body entity - top-level container."""
    entity_type: str = "body"
    lump_ref: Optional[int] = None
    wire_ref: Optional[int] = None
    transform_ref: Optional[int] = None
    bounding_box: Optional[List[float]] = None  # [x_min, y_min, z_min, x_max, y_max, z_max]


class AcisLump(AcisEntity):
    """ACIS lump entity - collection of shells."""
    entity_type: str = "lump"
    next_lump_ref: Optional[int] = None
    shell_ref: Optional[int] = None
    body_ref: Optional[int] = None
    bounding_box: Optional[List[float]] = None


class AcisShell(AcisEntity):
    """ACIS shell entity - collection of faces."""
    entity_type: str = "shell"
    next_shell_ref: Optional[int] = None
    subshell_ref: Optional[int] = None
    face_ref: Optional[int] = None
    wire_ref: Optional[int] = None
    lump_ref: Optional[int] = None
    bounding_box: Optional[List[float]] = None


class AcisSubshell(AcisEntity):
    """ACIS subshell entity."""
    entity_type: str = "subshell"
    next_subshell_ref: Optional[int] = None
    face_ref: Optional[int] = None
    shell_ref: Optional[int] = None


class AcisFace(AcisEntity):
    """ACIS face entity - surface bounded by loops."""
    entity_type: str = "face"
    next_face_ref: Optional[int] = None
    attrib_ref: Optional[int] = None
    shell_ref: Optional[int] = None
    subshell_ref: Optional[int] = None
    loop_ref: Optional[int] = None
    sense: SenseType = SenseType.FORWARD
    double_sided: bool = False
    containment: str = "out"
    surface_ref: Optional[int] = None
    bounding_box: Optional[List[float]] = None


class AcisLoop(AcisEntity):
    """ACIS loop entity - closed chain of coedges."""
    entity_type: str = "loop"
    next_loop_ref: Optional[int] = None
    attrib_ref: Optional[int] = None
    face_ref: Optional[int] = None
    coedge_ref: Optional[int] = None
    bounding_box: Optional[List[float]] = None


class AcisCoedge(AcisEntity):
    """ACIS coedge entity - oriented use of an edge."""
    entity_type: str = "coedge"
    next_coedge_ref: Optional[int] = None
    previous_coedge_ref: Optional[int] = None
    partner_coedge_ref: Optional[int] = None
    attrib_ref: Optional[int] = None
    loop_ref: Optional[int] = None
    edge_ref: Optional[int] = None
    sense: SenseType = SenseType.FORWARD


class AcisEdge(AcisEntity):
    """ACIS edge entity - curve segment between vertices."""
    entity_type: str = "edge"
    next_edge_ref: Optional[int] = None
    attrib_ref: Optional[int] = None
    start_vertex_ref: Optional[int] = None
    end_vertex_ref: Optional[int] = None
    coedge_ref: Optional[int] = None
    curve_ref: Optional[int] = None
    sense: SenseType = SenseType.FORWARD
    convexity: str = "unknown"
    bounding_box: Optional[List[float]] = None


class AcisVertex(AcisEntity):
    """ACIS vertex entity - point in space."""
    entity_type: str = "vertex"
    attrib_ref: Optional[int] = None
    edge_ref: Optional[int] = None
    point_ref: Optional[int] = None


class AcisPoint(AcisGeometricEntity):
    """ACIS point entity."""
    entity_type: str = "point"
    x: float
    y: float
    z: float


# Curve Entities

class AcisCurve(AcisGeometricEntity):
    """Base class for curve entities."""
    pass


class AcisStraightCurve(AcisCurve):
    """ACIS straight line curve."""
    entity_type: str = "straight-curve"
    origin: List[float] = Field(..., description="Start point [x, y, z]")
    direction: List[float] = Field(..., description="Direction vector [x, y, z]")


class AcisEllipseCurve(AcisCurve):
    """ACIS ellipse/circle curve."""
    entity_type: str = "ellipse-curve"
    center: List[float] = Field(..., description="Center point [x, y, z]")
    normal: List[float] = Field(..., description="Normal vector [x, y, z]")
    major_axis: List[float] = Field(..., description="Major axis vector [x, y, z]")
    radius_ratio: float = Field(1.0, description="Ratio of minor to major radius")


class AcisIntcurveCurve(AcisCurve):
    """ACIS intersection curve (typically B-spline)."""
    entity_type: str = "intcurve-curve"
    sense: SenseType = SenseType.FORWARD
    surface_ref: Optional[int] = None
    pcurve_ref: Optional[int] = None
    spline_data: Optional[AcisSplineCurveData] = None


class AcisSplineCurveData(BaseModel):
    """ACIS B-spline curve data."""
    subtype: str = "exactcur"  # exactcur, exppc, lawintcur, etc.
    curve_type: NurbsType = NurbsType.NURBS
    degree: int
    rational: bool = True
    closure_u: ClosureType = ClosureType.OPEN
    knots: List[float] = Field(default_factory=list)
    knot_multiplicities: List[float] = Field(default_factory=list)
    control_points: List[List[float]] = Field(default_factory=list)  # [[x, y, z, w], ...]
    start_param: float = 0.0
    end_param: float = 1.0


# Surface Entities

class AcisSurface(AcisGeometricEntity):
    """Base class for surface entities."""
    pass


class AcisPlaneSurface(AcisSurface):
    """ACIS planar surface."""
    entity_type: str = "plane-surface"
    origin: List[float] = Field(..., description="Origin point [x, y, z]")
    normal: List[float] = Field(..., description="Normal vector [x, y, z]")
    u_direction: List[float] = Field(..., description="U parameter direction [x, y, z]")
    v_direction: Optional[List[float]] = None  # Can be computed from normal and u_direction


class AcisConeSurface(AcisSurface):
    """ACIS conical surface."""
    entity_type: str = "cone-surface"
    origin: List[float]
    axis: List[float]
    major_axis: List[float]
    radius_ratio: float
    sine_angle: float
    cosine_angle: float


class AcisCylinderSurface(AcisSurface):
    """ACIS cylindrical surface."""
    entity_type: str = "cylinder-surface"
    origin: List[float]
    axis: List[float]
    major_axis: List[float]
    radius: float


class AcisSphereSurface(AcisSurface):
    """ACIS spherical surface."""
    entity_type: str = "sphere-surface"
    center: List[float]
    radius: float
    pole: List[float]
    equator: List[float]


class AcisTorusSurface(AcisSurface):
    """ACIS toroidal surface."""
    entity_type: str = "torus-surface"
    center: List[float]
    axis: List[float]
    major_axis: List[float]
    major_radius: float
    minor_radius: float


class AcisSplineSurface(AcisSurface):
    """ACIS B-spline surface."""
    entity_type: str = "spline-surface"
    sense: SenseType = SenseType.FORWARD
    spline_data: Optional[AcisSplineSurfaceData] = None


class AcisSplineSurfaceData(BaseModel):
    """ACIS B-spline surface data."""
    subtype: str = "exactsur"  # exactsur, exppc, etc.
    has_extra_zero: bool = False
    surface_type: NurbsType = NurbsType.NURBS
    u_degree: int
    v_degree: int
    rational: bool = True
    closure_u: ClosureType = ClosureType.OPEN
    closure_v: ClosureType = ClosureType.OPEN
    u_knots: List[float] = Field(default_factory=list)
    u_knot_multiplicities: List[float] = Field(default_factory=list)
    v_knots: List[float] = Field(default_factory=list)
    v_knot_multiplicities: List[float] = Field(default_factory=list)
    control_points: List[List[List[float]]] = Field(default_factory=list)  # [u][v][x,y,z,w]
    singular_u: List[bool] = Field(default_factory=list)  # Singularity flags
    singular_v: List[bool] = Field(default_factory=list)


# Attribute Entities

class AcisAttrib(AcisEntity):
    """Base class for attribute entities."""
    next_attrib_ref: Optional[int] = None
    owner_ref: Optional[int] = None


class AcisNameAttrib(AcisAttrib):
    """ACIS name attribute."""
    entity_type: str = "name_attrib"
    name: str = ""


class AcisStringAttrib(AcisAttrib):
    """ACIS string attribute."""
    entity_type: str = "string_attrib"
    value: str = ""


class AcisPositionAttrib(AcisAttrib):
    """ACIS position attribute."""
    entity_type: str = "position_attrib"
    position: List[float] = Field(default_factory=lambda: [0.0, 0.0, 0.0])


class AcisRgbColorAttrib(AcisAttrib):
    """ACIS RGB color attribute."""
    entity_type: str = "rgb_color-st-attrib"
    rgb: List[float] = Field(default_factory=lambda: [0.5, 0.5, 0.5])


# PCurve (Parameter Space Curve)

class AcisPCurve(AcisGeometricEntity):
    """ACIS parametric curve on a surface."""
    entity_type: str = "pcurve"
    surface_ref: Optional[int] = None
    intcurve_ref: Optional[int] = None
    spline_data: Optional[AcisSplineCurveData] = None


# Transform

class AcisTransform(AcisEntity):
    """ACIS transformation matrix."""
    entity_type: str = "transform"
    scale: float = 1.0
    rotation: Optional[List[List[float]]] = None  # 3x3 rotation matrix
    translation: Optional[List[float]] = None  # [x, y, z]
    shear: Optional[List[float]] = None


# Union types for convenience

AcisTopologyEntity = Union[
    AcisBody, AcisLump, AcisShell, AcisSubshell,
    AcisFace, AcisLoop, AcisCoedge, AcisEdge, AcisVertex
]

AcisCurveEntity = Union[
    AcisStraightCurve, AcisEllipseCurve, AcisIntcurveCurve
]

AcisSurfaceEntity = Union[
    AcisPlaneSurface, AcisConeSurface, AcisCylinderSurface,
    AcisSphereSurface, AcisTorusSurface, AcisSplineSurface
]

AcisAttributeEntity = Union[
    AcisNameAttrib, AcisStringAttrib, AcisPositionAttrib, AcisRgbColorAttrib
]


# Forward references for nested models
AcisIntcurveCurve.model_rebuild()
AcisSplineSurface.model_rebuild()

