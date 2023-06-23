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
