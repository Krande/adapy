import numpy as np

from ada import Plate
from ada.core.constants import O, X, Z
from ada.ifc.utils import (
    add_colour,
    create_ifc_placement,
    create_ifcindexpolyline,
    create_ifcpolyline,
    create_local_placement,
)


def write_ifc_plate(plate: Plate):
    if plate.parent is None:
        raise ValueError("Ifc element cannot be built without any parent element")

    a = plate.get_assembly()
    ifc_store = a.ifc_store
    f = ifc_store.f

    owner_history = ifc_store.owner_history
    parent = f.by_guid(plate.parent.guid)

    xvec = plate.poly.xdir
    zvec = plate.poly.normal
    yvec = np.cross(zvec, xvec)

    # Wall creation: Define the wall shape as a polyline axis and an extruded area solid
    plate_placement = create_local_placement(f, relative_to=parent.ObjectPlacement)

    tra_mat = np.array([xvec, yvec, zvec])
    t_vec = [0, 0, plate.t]
    origin = np.array(plate.poly.placement.origin)
    res = origin + np.dot(tra_mat, t_vec)
    polyline = create_ifcpolyline(f, [origin.astype(float).tolist(), res.tolist()])
    axis_representation = f.createIfcShapeRepresentation(ifc_store.get_context("Axis"), "Axis", "Curve2D", [polyline])
    extrusion_placement = create_ifc_placement(f, O, Z, X)
    points = [(float(n[0]), float(n[1]), float(n[2])) for n in plate.poly.seg_global_points]
    seg_index = plate.poly.seg_index
    polyline = create_ifcindexpolyline(f, points, seg_index)
    # polyline = plate.create_ifcpolyline(f, point_list)
    ifcclosedprofile = f.createIfcArbitraryClosedProfileDef("AREA", None, polyline)

    ifcdir = f.createIfcDirection(zvec.astype(float).tolist())
    ifcextrudedareasolid = f.createIfcExtrudedAreaSolid(ifcclosedprofile, extrusion_placement, ifcdir, plate.t)

    body = f.createIfcShapeRepresentation(ifc_store.get_context("Body"), "Body", "SolidModel", [ifcextrudedareasolid])

    product_shape = f.createIfcProductDefinitionShape(None, None, [axis_representation, body])

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
    if plate.colour is not None:
        add_colour(f, ifcextrudedareasolid, str(plate.colour), plate.colour)

    # Material
    ifc_store.writer.associate_elem_with_material(plate.material, ifc_plate)

    return ifc_plate
