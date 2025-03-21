from __future__ import annotations

import ifcopenshell

from ada.cadit.ifc.write.geom.placement import (
    direction,
    ifc_placement_from_axis3d,
    point,
)
from ada.geom import solids as geo_so
from ada.geom import surfaces as geo_su

from .surfaces import arbitrary_profile_def


def extruded_area_solid(
    eas: geo_so.ExtrudedAreaSolid, f: ifcopenshell.file, profile: ifcopenshell.entity_instance = None
) -> ifcopenshell.entity_instance:
    """Converts an ExtrudedAreaSolid to an IFC representation"""

    axis3d = ifc_placement_from_axis3d(eas.position, f)
    if profile is not None:
        pass
    elif isinstance(eas.swept_area, geo_su.ArbitraryProfileDef):
        profile = arbitrary_profile_def(eas.swept_area, f)
    else:
        raise NotImplementedError(f"Unsupported swept area type: {type(eas.swept_area)}")

    extrude_direction = direction(eas.extruded_direction, f)
    return f.create_entity("IfcExtrudedAreaSolid", profile, axis3d, extrude_direction, eas.depth)


def extruded_area_solid_tapered(
    eas: geo_so.ExtrudedAreaSolidTapered, f: ifcopenshell.file
) -> ifcopenshell.entity_instance:
    """Converts an ExtrudedAreaSolidTapered to an IFC representation"""

    axis3d = ifc_placement_from_axis3d(eas.position, f)
    profile = arbitrary_profile_def(eas.swept_area, f)
    end_profile = arbitrary_profile_def(eas.end_swept_area, f)
    extrude_direction = direction(eas.extruded_direction, f)

    return f.create_entity(
        "IfcExtrudedAreaSolidTapered",
        SweptArea=profile,
        Position=axis3d,
        ExtrudedDirection=extrude_direction,
        Depth=eas.depth,
        EndSweptArea=end_profile,
    )


def revolved_area_solid(ras: geo_so.RevolvedAreaSolid, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a RevolvedAreaSolid to an IFC representation"""
    import math

    axis3d = ifc_placement_from_axis3d(ras.position, f)

    if isinstance(ras.swept_area, geo_su.ArbitraryProfileDef):
        profile = arbitrary_profile_def(ras.swept_area, f)
    else:
        raise NotImplementedError(f"Unsupported swept area type: {type(ras.swept_area)}")

    revolve_point = point(ras.axis.location, f)
    rev_axis_dir = direction(ras.axis.axis, f)
    revolve_axis1 = f.create_entity("IfcAxis1Placement", revolve_point, rev_axis_dir)
    angle = math.radians(ras.angle)

    return f.create_entity("IfcRevolvedAreaSolid", SweptArea=profile, Position=axis3d, Axis=revolve_axis1, Angle=angle)
