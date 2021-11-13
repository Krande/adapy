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


def wall_to_ifc(wall: Wall):
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
    polyline = create_ifcpolyline(f, wall.points)
    axis_representation = f.createIfcShapeRepresentation(context, "Axis", "Curve2D", [polyline])

    extrusion_placement = create_ifc_placement(f, (0.0, 0.0, float(elevation)), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))

    polyline = create_ifcpolyline(f, wall.extrusion_area)
    profile = f.createIfcArbitraryClosedProfileDef("AREA", None, polyline)

    solid = create_ifcextrudedareasolid(f, profile, extrusion_placement, (0.0, 0.0, 1.0), wall.height)
    body = f.createIfcShapeRepresentation(context, "Body", "SweptSolid", [solid])

    if "hidden" in wall.metadata.keys():
        if wall.metadata["hidden"] is True:
            a.presentation_layers.append(body)

    product_shape = f.createIfcProductDefinitionShape(None, None, [axis_representation, body])

    wall_el = f.createIfcWall(
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
            insert_el = add_ifc_insert_elem(wall, insert, opening_element, wall_el)
            elements.append(opening_element)
            elements.append(insert_el)

    f.createIfcRelContainedInSpatialStructure(
        create_guid(),
        owner_history,
        "Wall Elements",
        None,
        [wall_el] + elements,
        parent,
    )

    props = create_property_set("Properties", f, wall.metadata)
    f.createIfcRelDefinesByProperties(
        create_guid(),
        owner_history,
        "Properties",
        None,
        [wall_el],
        props,
    )

    return wall_el


def add_ifc_insert_elem(wall: Wall, insert, opening_element, wall_el):

    a = wall.parent.get_assembly()
    f = a.ifc_file

    context = f.by_type("IfcGeometricRepresentationContext")[0]
    owner_history = a.user.to_ifc()
    schema = a.ifc_file.wrapped_data.schema

    # Create a simplified representation for the Window
    insert_placement = create_local_placement(f, O, Z, X, wall_el.ObjectPlacement)
    if len(insert.shapes) > 1:
        raise ValueError("More than 1 shape is currently not allowed for Wall inserts")
    shape = insert.shapes[0].geom
    insert_shape = tesselate_shape(shape, schema, get_tolerance(a.units))
    # Link to representation context
    for rep in insert_shape.Representations:
        rep.ContextOfItems = context

    ifc_type = insert.metadata["ifc_type"]

    if ifc_type == "IfcWindow":
        ifc_insert = f.createIfcWindow(
            create_guid(),
            owner_history,
            "Window",
            "An awesome window",
            None,
            insert_placement,
            insert_shape,
            None,
            None,
        )
    elif ifc_type == "IfcDoor":
        ifc_insert = f.createIfcDoor(
            create_guid(),
            owner_history,
            "Door",
            "An awesome Door",
            None,
            insert_placement,
            insert_shape,
            None,
            None,
        )
    else:
        raise ValueError(f'Currently unsupported ifc_type "{ifc_type}"')

    # Relate the window to the opening element
    f.createIfcRelFillsElement(
        create_guid(),
        owner_history,
        None,
        None,
        opening_element,
        ifc_insert,
    )
    return ifc_insert
