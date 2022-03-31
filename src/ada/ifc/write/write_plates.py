import numpy as np

from ada import Plate
from ada.core.constants import O, X, Z
from ada.ifc.utils import (
    add_colour,
    add_multiple_props_to_elem,
    create_guid,
    create_ifc_placement,
    create_ifcindexpolyline,
    create_ifcpolyline,
    create_local_placement,
)


def write_ifc_plate(plate: Plate):
    if plate.parent is None:
        raise ValueError("Ifc element cannot be built without any parent element")

    a = plate.parent.get_assembly()
    f = a.ifc_file

    context = f.by_type("IfcGeometricRepresentationContext")[0]
    owner_history = a.user.to_ifc()
    parent = plate.parent.get_ifc_elem()

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
    axis_representation = f.createIfcShapeRepresentation(context, "Axis", "Curve2D", [polyline])
    extrusion_placement = create_ifc_placement(f, O, Z, X)
    points = [(float(n[0]), float(n[1]), float(n[2])) for n in plate.poly.seg_global_points]
    seg_index = plate.poly.seg_index
    polyline = create_ifcindexpolyline(f, points, seg_index)
    # polyline = plate.create_ifcpolyline(f, point_list)
    ifcclosedprofile = f.createIfcArbitraryClosedProfileDef("AREA", None, polyline)

    ifcdir = f.createIfcDirection(zvec.astype(float).tolist())
    ifcextrudedareasolid = f.createIfcExtrudedAreaSolid(ifcclosedprofile, extrusion_placement, ifcdir, plate.t)

    body = f.createIfcShapeRepresentation(context, "Body", "SolidModel", [ifcextrudedareasolid])

    if "hidden" in plate.metadata.keys():
        if plate.metadata["hidden"] is True:
            a.presentation_layers.append(body)

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

    plate._ifc_elem = ifc_plate

    # Add colour
    if plate.colour is not None:
        add_colour(f, ifcextrudedareasolid, str(plate.colour), plate.colour)

    # Add penetrations
    # elements = []
    for pen in plate.penetrations:
        # elements.append(pen.ifc_opening)
        f.createIfcRelVoidsElement(
            create_guid(),
            owner_history,
            None,
            None,
            ifc_plate,
            pen.ifc_opening,
        )

    # Material
    f.create_entity(
        "IfcRelAssociatesMaterial",
        create_guid(),
        owner_history,
        plate.material.name,
        plate.name,
        [ifc_plate],
        plate.material.ifc_mat,
    )

    # if "props" in plate.metadata.keys():
    if plate.ifc_options.export_props is True:
        add_multiple_props_to_elem(plate.metadata.get("props", dict()), ifc_plate, f, owner_history)

    return ifc_plate
