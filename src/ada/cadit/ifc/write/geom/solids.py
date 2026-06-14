from __future__ import annotations

import ifcopenshell

from ada.cadit.ifc.write.geom.placement import (
    direction,
    ifc_placement_from_axis3d,
    point,
)
from ada.geom import curves as geo_cu
from ada.geom import solids as geo_so
from ada.geom import surfaces as geo_su

from .curves import circle_curve, indexed_poly_curve, poly_line
from .surfaces import arbitrary_profile_def, create_closed_shell


def faceted_brep(fb: geo_so.FacetedBrep, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a FacetedBrep to an IfcFacetedBrep, or IfcFacetedBrepWithVoids when it has
    inner void shells."""
    outer = create_closed_shell(fb.outer, f)
    if not fb.voids:
        return f.create_entity("IfcFacetedBrep", Outer=outer)
    voids = [create_closed_shell(v, f) for v in fb.voids]
    return f.create_entity("IfcFacetedBrepWithVoids", Outer=outer, Voids=voids)


def _directrix(curve: geo_cu.CURVE_GEOM_TYPES, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Write a sweep directrix curve to its matching IFC curve entity."""
    if isinstance(curve, geo_cu.IndexedPolyCurve):
        return indexed_poly_curve(curve, f)
    elif isinstance(curve, geo_cu.PolyLine):
        return poly_line(curve, f)
    elif isinstance(curve, geo_cu.Circle):
        return circle_curve(curve, f)
    raise NotImplementedError(f"Unsupported directrix curve type: {type(curve)}")


def fixed_reference_swept_area_solid(
    frs: geo_so.FixedReferenceSweptAreaSolid, f: ifcopenshell.file
) -> ifcopenshell.entity_instance:
    """Converts a FixedReferenceSweptAreaSolid to an IfcFixedReferenceSweptAreaSolid.

    adapy's geom model doesn't track the fixed-reference direction (the OCC build derives
    orientation from the directrix), so FixedReference is emitted from the position's
    ref_direction to satisfy the (mandatory) IFC attribute."""
    from ada.geom.direction import Direction

    ref = frs.position.ref_direction if frs.position.ref_direction is not None else Direction(1, 0, 0)
    return f.create_entity(
        "IfcFixedReferenceSweptAreaSolid",
        SweptArea=arbitrary_profile_def(frs.swept_area, f),
        Position=ifc_placement_from_axis3d(frs.position, f),
        Directrix=_directrix(frs.directrix, f),
        FixedReference=direction(ref, f),
    )


def swept_disk_solid(sds: geo_so.SweptDiskSolid, f: ifcopenshell.file) -> ifcopenshell.entity_instance:
    """Converts a SweptDiskSolid to an IfcSweptDiskSolid."""
    kwargs = dict(Directrix=_directrix(sds.directrix, f), Radius=float(sds.radius))
    if sds.inner_radius is not None:
        kwargs["InnerRadius"] = float(sds.inner_radius)
    if sds.start_param is not None:
        kwargs["StartParam"] = float(sds.start_param)
    if sds.end_param is not None:
        kwargs["EndParam"] = float(sds.end_param)
    return f.create_entity("IfcSweptDiskSolid", **kwargs)


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
