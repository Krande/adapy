from __future__ import annotations

from typing import TYPE_CHECKING

from ada import Plate, PlateCurved
from ada.cadit.ifc.utils import add_colour
from ada.cadit.ifc.write.geom.placement import ifc_placement_from_axis3d
from ada.cadit.ifc.write.geom.solids import extruded_area_solid
from ada.cadit.ifc.write.geom.surfaces import advanced_face

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore


from ada.config import logger


def update_ifc_plate(ifc_store: IfcStore, plate: Plate):
    logger.warning("Updating IFC plate not implemented yet")


def _plate_body(plate: Plate, f) -> "ifcopenshell.entity_instance":  # noqa: F821 - typing-only name
    """The plate's IFC body: an IfcExtrudedAreaSolid, or an IfcAdvancedBrep when the outline carries an
    analytic B-spline edge.

    IfcExtrudedAreaSolid can't take a spline boundary through the tools that matter —
    IfcIndexedPolyCurve is line/arc-only, and ifcopenshell's geometry engine won't build a wire from a
    B-spline IfcCompositeCurve segment — so those plates emit the exact analytic B-rep instead
    (planar caps/sides + the spline side face as the degree-1-in-v extrusion surface).
    """
    from ada.api.curves import SplineSegment
    from ada.cadit.ifc.write.geom.surfaces import create_closed_shell
    from ada.geom.primitive_brep import extruded_loop_to_shell

    segs = plate.poly.segments3d
    if any(isinstance(s, SplineSegment) for s in segs):
        shell = extruded_loop_to_shell(segs, plate.poly.normal, plate.t)
        if shell is not None:
            return f.create_entity("IfcAdvancedBrep", Outer=create_closed_shell(shell, f))
        logger.warning("plate %r: analytic B-rep build failed; falling back to the extruded solid", plate.name)
    return extruded_area_solid(plate.solid_geom().geometry, f)


def write_ifc_plate(ifc_store: IfcStore, plate: Plate):
    if plate.parent is None:
        raise ValueError("Ifc element cannot be built without any parent element")

    owner_history = ifc_store.owner_history
    f = ifc_store.f

    ori = plate.placement.to_axis2placement3d()
    axis2placement = ifc_placement_from_axis3d(ori, f)

    plate_placement = f.create_entity("IfcLocalPlacement", PlacementRelTo=None, RelativePlacement=axis2placement)

    solid = _plate_body(plate, f)
    body = f.createIfcShapeRepresentation(ifc_store.get_context("Body"), "Body", "SolidModel", [solid])

    product_shape = f.create_entity("IfcProductDefinitionShape", None, None, [body])

    ifc_plate = f.create_entity(
        "IfcPlate",
        GlobalId=plate.guid,
        OwnerHistory=owner_history,
        Name=plate.name,
        Description=plate.name,
        ObjectType=None,
        ObjectPlacement=plate_placement,
        Representation=product_shape,
        Tag=None,
    )

    # Add colour
    if plate.color is not None:
        add_colour(f, solid, str(plate.color), plate.color)

    return ifc_plate


def write_ifc_plate_curved(ifc_store: IfcStore, plate: PlateCurved):
    if plate.parent is None:
        raise ValueError("Ifc element cannot be built without any parent element")

    owner_history = ifc_store.owner_history
    f = ifc_store.f

    ori = plate.placement.to_axis2placement3d()
    axis2placement = ifc_placement_from_axis3d(ori, f)

    plate_placement = f.create_entity("IfcLocalPlacement", PlacementRelTo=None, RelativePlacement=axis2placement)

    solid = advanced_face(plate.geom.geometry, f)
    body = f.createIfcShapeRepresentation(ifc_store.get_context("Body"), "Body", "SolidModel", [solid])

    product_shape = f.create_entity("IfcProductDefinitionShape", None, None, [body])

    # Use keyword args to avoid positional mistakes and ensure GlobalId is correct.
    ifc_plate = f.create_entity(
        "IfcPlate",
        GlobalId=plate.guid,
        OwnerHistory=owner_history,
        Name=plate.name,
        Description=plate.name,
        ObjectType=None,
        ObjectPlacement=plate_placement,
        Representation=product_shape,
        Tag=None,
    )

    # Add colour
    if plate.color is not None:
        add_colour(f, solid, str(plate.color), plate.color)

    return ifc_plate
