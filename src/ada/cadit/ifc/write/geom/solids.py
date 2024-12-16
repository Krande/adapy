from __future__ import annotations

import ifcopenshell

from ada.cadit.ifc.write.geom.placement import direction, ifc_placement_from_axis3d
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
