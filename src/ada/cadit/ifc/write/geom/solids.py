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
    """Converts a RevolvedAreaSolid to an IFC representation.

    The geom carries the revolution axis in global coordinates (the convention
    both CAD backends build from). ``IfcRevolvedAreaSolid.Axis`` is defined in the
    ``Position`` coordinate system, so we transform global -> local here — keeping
    the geom backend-native while emitting spec-correct IFC.
    """
    import math

    axis3d = ifc_placement_from_axis3d(ras.position, f)

    if isinstance(ras.swept_area, geo_su.ArbitraryProfileDef):
        profile = arbitrary_profile_def(ras.swept_area, f)
    else:
        raise NotImplementedError(f"Unsupported swept area type: {type(ras.swept_area)}")

    loc_pt, loc_dir = _axis_global_to_position_local(ras)
    revolve_axis1 = f.create_entity("IfcAxis1Placement", point(loc_pt, f), direction(loc_dir, f))
    angle = math.radians(ras.angle)

    return f.create_entity("IfcRevolvedAreaSolid", SweptArea=profile, Position=axis3d, Axis=revolve_axis1, Angle=angle)


def _axis_global_to_position_local(ras: geo_so.RevolvedAreaSolid):
    """Express the (global) revolution axis in ``ras.position``'s local frame."""
    import numpy as np

    pos = ras.position
    xdir = np.asarray(pos.ref_direction, dtype=float)
    zdir = np.asarray(pos.axis, dtype=float)
    xdir = xdir / np.linalg.norm(xdir)
    zdir = zdir / np.linalg.norm(zdir)
    ydir = np.cross(zdir, xdir)
    rot = np.column_stack([xdir, ydir, zdir])  # local -> global

    origin = np.asarray(pos.location, dtype=float)
    ax_loc = np.asarray(ras.axis.location, dtype=float)
    ax_dir = np.asarray(ras.axis.axis, dtype=float)

    local_loc = rot.T @ (ax_loc - origin)
    local_dir = rot.T @ ax_dir
    return tuple(float(x) for x in local_loc), tuple(float(x) for x in local_dir)
