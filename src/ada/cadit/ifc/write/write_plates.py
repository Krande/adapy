from ada import Plate
from ada.cadit.ifc.utils import (
    add_colour,
    create_ifc_placement,
    create_ifcindexpolyline,
    create_local_placement,
    to_real,
)
from ada.cadit.ifc.write.geom.placement import ifc_placement_from_axis3d
from ada.cadit.ifc.write.geom.solids import extruded_area_solid


def write_ifc_plate(plate: Plate):
    if plate.parent is None:
        raise ValueError("Ifc element cannot be built without any parent element")

    a = plate.get_assembly()
    ifc_store = a.ifc_store
    owner_history = ifc_store.owner_history
    f = ifc_store.f
    parent = f.by_guid(plate.parent.guid)

    ori = plate.poly.orientation.to_axis2placement3d()
    axis2placement = ifc_placement_from_axis3d(ori, f)
    plate_placement = f.create_entity(
        "IfcLocalPlacement", PlacementRelTo=parent.ObjectPlacement, RelativePlacement=axis2placement
    )
    representations = []

    # Todo: Begin implementing IFC plate from neutral geom definition

    solid = extruded_area_solid(plate.solid_geom().geometry, f)
    body = f.createIfcShapeRepresentation(ifc_store.get_context("Body"), "Body", "SolidModel", [solid])
    representations.append(body)

    product_shape = f.create_entity("IfcProductDefinitionShape", None, None, representations)

    ifc_plate = f.create_entity(
        "IfcPlate",
        plate.guid,
        owner_history,
        plate.name,
        plate.name,
        None,
        plate_placement,
        product_shape,
        None,
    )

    # Add colour
    if plate.color is not None:
        add_colour(f, solid, str(plate.color), plate.color)

    # Material
    ifc_store.writer.associate_elem_with_material(plate.material, ifc_plate)

    return ifc_plate
