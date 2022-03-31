from ada import Part, Wall
from ada.core.constants import O, X, Z
from ada.ifc.utils import (
    add_negative_extrusion,
    create_guid,
    create_ifc_placement,
    create_ifcextrudedareasolid,
    create_ifcpolyline,
    create_local_placement,
    create_property_set,
    get_tolerance,
    tesselate_shape,
)

from .write_stru_components import write_door, write_window


def write_ifc_wall(wall: Wall):
    if wall.parent is None:
        raise ValueError("Ifc element cannot be built without any parent element")

    a = wall.parent.get_assembly()
    f = a.ifc_file

    context = f.by_type("IfcGeometricRepresentationContext")[0]
    owner_history = a.user.to_ifc()
    parent = wall.parent.get_ifc_elem()
    elevation = wall.placement.origin[2]

    # Wall creation: Define the wall shape as a polyline axis and an extruded area solid
    wall_placement = create_local_placement(f, relative_to=parent.ObjectPlacement)

    # polyline = wall.create_ifcpolyline(f, [(0.0, 0.0, 0.0), (5.0, 0.0, 0.0)])
    polyline2d = create_ifcpolyline(f, wall.points)
    axis_representation = f.createIfcShapeRepresentation(context, "Axis", "Curve2D", [polyline2d])

    extrusion_placement = create_ifc_placement(f, (0.0, 0.0, float(elevation)), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))

    polyline = create_ifcpolyline(f, wall.extrusion_area)
    profile = f.createIfcArbitraryClosedProfileDef("AREA", None, polyline)

    solid = create_ifcextrudedareasolid(f, profile, extrusion_placement, (0.0, 0.0, 1.0), wall.height)
    body = f.createIfcShapeRepresentation(context, "Body", "SweptSolid", [solid])

    if "hidden" in wall.metadata.keys():
        if wall.metadata["hidden"] is True:
            a.presentation_layers.append(body)

    product_shape = f.createIfcProductDefinitionShape(None, None, [axis_representation, body])

    wall_el = f.create_entity(
        "IfcWall",
        wall.guid,
        owner_history,
        wall.name,
        "An awesome wall",
        None,
        wall_placement,
        product_shape,
        None,
    )

    # Check for penetrations
    elements = []
    if len(wall.inserts) > 0:
        for i, insert in enumerate(wall.inserts):
            opening_element = add_negative_extrusion(f, O, Z, X, insert.height, wall.openings_extrusions[i], wall_el)
            if issubclass(type(insert), Part) is False:
                raise ValueError(f'Unrecognized type "{type(insert)}"')
            elements.append(opening_element)
            # for shape_ in insert.shapes:
            #     insert_el = add_ifc_insert_elem(wall, shape_, opening_element, wall_el, insert.metadata["ifc_type"])
            #     elements.append(insert_el)

    f.createIfcRelContainedInSpatialStructure(
        create_guid(),
        owner_history,
        "Wall Elements",
        None,
        [wall_el] + elements,
        parent,
    )

    if wall.ifc_options.export_props is True:
        props = create_property_set("Properties", f, wall.metadata, owner_history)
        f.createIfcRelDefinesByProperties(
            create_guid(),
            owner_history,
            "Properties",
            None,
            [wall_el],
            props,
        )

    return wall_el


def add_ifc_insert_elem(wall: Wall, shape_, opening_element, wall_el, ifc_type):

    a = wall.parent.get_assembly()
    f = a.ifc_file

    context = f.by_type("IfcGeometricRepresentationContext")[0]
    owner_history = a.user.to_ifc()
    schema = a.ifc_file.wrapped_data.schema

    # Create a simplified representation for the Window
    insert_placement = create_local_placement(f, O, Z, X, wall_el.ObjectPlacement)

    shape = shape_.geom

    insert_shape_ = tesselate_shape(shape, schema, get_tolerance(a.units))
    insert_shape = f.add(insert_shape_)

    # Link to representation context
    for rep in insert_shape.Representations:
        rep.ContextOfItems = context

    insert_map = dict(IfcWindow=write_window, IfcDoor=write_door)

    insert_writer = insert_map.get(ifc_type, None)

    if insert_writer is None:
        raise ValueError(f'Currently unsupported ifc_type "{ifc_type}"')

    ifc_insert = insert_writer(f, owner_history, insert_placement, insert_shape)

    # Relate the window to the opening element
    f.create_entity(
        "IfcRelFillsElement",
        create_guid(),
        owner_history,
        None,
        None,
        opening_element,
        ifc_insert,
    )
    return ifc_insert
