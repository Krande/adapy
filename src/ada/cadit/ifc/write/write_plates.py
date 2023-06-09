import numpy as np

from ada import Plate
from ada.cadit.ifc.utils import (
    add_colour,
    create_ifc_placement,
    create_ifcindexpolyline,
    create_ifcpolyline,
    create_local_placement, to_real,
)
from ada.cadit.ifc.geom.placement import ifc_placement_from_axis3d
from ada.core.constants import O, X, Z
from ada.cadit.ifc.geom.solids import extruded_area_solid

def write_ifc_plate(plate: Plate):
    if plate.parent is None:
        raise ValueError("Ifc element cannot be built without any parent element")

    a = plate.get_assembly()
    ifc_store = a.ifc_store
    f = ifc_store.f
    ori = plate.poly.orientation
    owner_history = ifc_store.owner_history
    parent = f.by_guid(plate.parent.guid)

    # Todo: Begin implementing IFC plate from neutral geom definition
    # plate_geometry = plate.solid_geom()
    # solid = extruded_area_solid(plate_geometry.geometry, f)

    # Wall creation: Define the wall shape as a polyline axis and an extruded area solid
    plate_placement = create_local_placement(f)#, relative_to=parent.ObjectPlacement)
    representations = []

    # t_vec = [0, 0, plate.t]
    # axis_end = ori.transform_local_points_back_to_global([t_vec])
    # polyline = create_ifcpolyline(f, [ori.origin, axis_end[0]])
    # axis_representation = f.createIfcShapeRepresentation(ifc_store.get_context("Axis"), "Axis", "Curve2D", [polyline])
    # representations.append(axis_representation)

    extrusion_placement = create_ifc_placement(f, ori.origin)

    seg_points = [(float(n[0]), float(n[1]), float(n[2])) for n in plate.poly.seg_global_points]
    seg_index = plate.poly.seg_index
    polyline = create_ifcindexpolyline(f, seg_points, seg_index)

    # polyline = plate.create_ifcpolyline(f, point_list)
    ifcclosedprofile = f.createIfcArbitraryClosedProfileDef("AREA", None, polyline)

    ifcdir = f.createIfcDirection(to_real(ori.zdir))
    ifcextrudedareasolid = f.createIfcExtrudedAreaSolid(ifcclosedprofile, extrusion_placement, ifcdir, plate.t)

    body = f.createIfcShapeRepresentation(ifc_store.get_context("Body"), "Body", "SolidModel", [ifcextrudedareasolid])
    representations.append(body)

    product_shape = f.createIfcProductDefinitionShape(None, None, representations)

    ifc_plate = f.createIfcPlate(
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
        add_colour(f, ifcextrudedareasolid, str(plate.color), plate.color)

    # Material
    ifc_store.writer.associate_elem_with_material(plate.material, ifc_plate)

    return ifc_plate
