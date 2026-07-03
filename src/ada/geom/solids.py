from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

from ada.core.vector_utils import create_right_hand_vectors_xv_yv_from_zv
from ada.geom.curves import CURVE_GEOM_TYPES
from ada.geom.direction import Direction
from ada.geom.placement import Axis1Placement, Axis2Placement3D
from ada.geom.points import Point
from ada.geom.surfaces import (
    SURFACE_GEOM_TYPES,
    ArbitraryProfileDef,
    ClosedShell,
    ConnectedFaceSet,
    ProfileDef,
)


@dataclass(slots=True)
class ExtrudedAreaSolid:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcExtrudedAreaSolid.htm)
    STEP AP242 (https://www.steptools.com/stds/stp_aim/html/t_extruded_area_solid.html)
    """

    swept_area: ProfileDef | ArbitraryProfileDef
    position: Axis2Placement3D
    depth: float
    extruded_direction: Direction


@dataclass(slots=True)
class ExtrudedAreaSolidTapered(ExtrudedAreaSolid):
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcExtrudedAreaSolidTapered.htm)
    """

    end_swept_area: ProfileDef


@dataclass(slots=True)
class RevolvedAreaSolid:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcRevolvedAreaSolid.htm)
    STEP AP242 (https://www.steptools.com/stds/stp_aim/html/t_revolved_area_solid.html)
    """

    swept_area: ProfileDef | SURFACE_GEOM_TYPES
    position: Axis2Placement3D
    axis: Axis1Placement
    angle: float


@dataclass(slots=True)
class FixedReferenceSweptAreaSolid:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcFixedReferenceSweptAreaSolid.htm)
    STEP AP242 (https://www.steptools.com/stds/stp_aim/html/t_surface_curve_swept_area_solid.html)
    """

    swept_area: SURFACE_GEOM_TYPES
    position: Axis2Placement3D
    directrix: CURVE_GEOM_TYPES
    # The reference direction that keeps the swept profile from twisting along the directrix (no
    # Frenet roll). IFC default is +Z. StartParam/EndParam are intentionally not modelled — for a
    # bounded directrix (e.g. IfcGradientCurve) IfcOpenShell sweeps the whole curve regardless, and
    # we match that.
    fixed_reference: Direction = field(default_factory=lambda: Direction(0.0, 0.0, 1.0))


@dataclass(slots=True)
class SweptDiskSolid:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcSweptDiskSolid.htm)
    STEP AP242 (https://www.steptools.com/stds/stp_aim/html/t_swept_disk_solid.html)

    A circular — or annular, when ``inner_radius`` is set — disk swept along the ``directrix``
    curve. The canonical representation for pipes/rods. ``IfcSweptDiskSolidPolygonal`` (a
    polyline directrix with a fillet radius) maps here too, with ``fillet_radius`` set.
    """

    directrix: CURVE_GEOM_TYPES
    radius: float
    inner_radius: float | None = None
    start_param: float | None = None
    end_param: float | None = None
    fillet_radius: float | None = None


@dataclass(slots=True)
class Box:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcBlock.htm)
    STEP AP242 (https://www.steptools.com/stds/stp_aim/html/t_box_domain.html)
    """

    position: Axis2Placement3D
    x_length: float
    y_length: float
    z_length: float

    @staticmethod
    def from_xyz_and_dims(x, y, z, x_length: float, y_length: float, z_length: float, d1=None, d2=None) -> Box:
        d1 = d1 if d1 is not None else Direction(0, 0, 1)
        d2 = d2 if d2 is not None else Direction(1, 0, 0)
        axis3d = Axis2Placement3D(Point(x, y, z), d1, d2)
        return Box(axis3d, x_length, y_length, z_length)

    @staticmethod
    def from_2points(p1: Point, p2: Point) -> Box:
        x = min(p1.x, p2.x)
        y = min(p1.y, p2.y)
        z = min(p1.z, p2.z)
        x_length = abs(p1.x - p2.x)
        y_length = abs(p1.y - p2.y)
        z_length = abs(p1.z - p2.z)
        return Box.from_xyz_and_dims(x, y, z, x_length, y_length, z_length)


@dataclass(slots=True)
class RectangularPyramid:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcRectangularPyramid.htm)
    STEP AP242 (https://www.steptools.com/stds/stp_aim/html/t_pyramid_volume.html)
    """

    position: Axis2Placement3D
    x_length: float
    y_length: float
    z_length: float


@dataclass(slots=True)
class Cone:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcRightCircularCone.htm)
    STEP AP242 (https://www.steptools.com/stds/stp_aim/html/t_right_circular_cone.html)
    """

    position: Axis2Placement3D
    bottom_radius: float
    height: float

    @staticmethod
    def from_2points(p1: Point, p2: Point, r: float) -> Cone:
        vec = Direction(*(p2 - p1))
        axis = vec.get_normalized()
        xv, yv = create_right_hand_vectors_xv_yv_from_zv(axis)
        height = vec.get_length()
        axis3d = Axis2Placement3D(p1, axis, xv)
        return Cone(axis3d, r, height)


@dataclass(slots=True)
class Cylinder:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcRightCircularCylinder.htm)
    STEP AP242 (https://www.steptools.com/stds/stp_aim/html/t_right_circular_cylinder.html)
    """

    position: Axis2Placement3D
    radius: float
    height: float

    @staticmethod
    def from_2points(p1: Point, p2: Point, r: float) -> Cylinder:
        vec = Direction(*(p2 - p1))
        axis = vec.get_normalized()
        xv, yv = create_right_hand_vectors_xv_yv_from_zv(axis)
        height = vec.get_length()
        axis3d = Axis2Placement3D(p1, axis, xv)
        return Cylinder(axis3d, r, height)


@dataclass(slots=True)
class Sphere:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3_0_0/lexical/IfcSphere.htm)
    STEP AP242 (https://www.steptools.com/stds/stp_aim/html/t_sphere.html)
    """

    center: Point
    radius: float


@dataclass(slots=True)
class Torus:
    """STEP AP242 https://www.steptools.com/stds/stp_aim/html/t_torus.html

    A CSG torus primitive (axis position + major/minor radii). No IFC equivalent.
    """

    position: Axis1Placement
    major_radius: float
    minor_radius: float


@dataclass(slots=True)
class AdvancedBrep:
    """
    IFC4x3 (https://ifc43-docs.standards.buildingsmart.org/IFC/RELEASE/IFC4x3/HTML/lexical/IfcAdvancedBrep.htm)
    """

    outer: ConnectedFaceSet


@dataclass(slots=True)
class FacetedBrep:
    """
    IFC4x3 (https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/HTML/lexical/IfcFacetedBrep.htm)

    A manifold solid bounded by planar polygonal faces — the most common B-rep encoding. Maps to
    ``IfcFacetedBrepWithVoids`` when ``voids`` (inner closed shells) are present.
    """

    outer: ClosedShell
    voids: list[ClosedShell] = field(default_factory=list)


SOLID_GEOM_TYPES = Union[
    ExtrudedAreaSolid,
    RevolvedAreaSolid,
    Box,
    RectangularPyramid,
    Cone,
    Cylinder,
    Sphere,
    FixedReferenceSweptAreaSolid,
    AdvancedBrep,
    FacetedBrep,
    ExtrudedAreaSolidTapered,
    SweptDiskSolid,
]
