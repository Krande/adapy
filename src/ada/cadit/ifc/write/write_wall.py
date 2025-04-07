from ada import Wall
from ada.base.units import Units
from ada.cadit.ifc.utils import (
    create_ifc_placement,
    create_ifcextrudedareasolid,
    create_ifcpolyline,
    create_local_placement,
    tesselate_shape,
)
from ada.core.constants import O, X, Z
from ada.core.guid import create_guid

from .write_building_components import write_door, write_window


def write_ifc_wall(wall: Wall):
    if wall.parent is None:
        raise ValueError("Ifc element cannot be built without any parent element")

    a = wall.parent.get_assembly()
    ifc_store = a.ifc_store
    f = ifc_store.f

    owner_history = ifc_store.owner_history
    parent = f.by_guid(wall.parent.guid)
    elevation = wall.placement.origin[2]

    # Wall creation: Define the wall shape as a polyline axis and an extruded area solid
    wall_placement = create_local_placement(f, relative_to=parent.ObjectPlacement)

    polyline2d = create_ifcpolyline(f, wall.points)
    axis_representation = f.createIfcShapeRepresentation(ifc_store.get_context("Axis"), "Axis", "Curve2D", [polyline2d])

    extrusion_placement = create_ifc_placement(f, (0.0, 0.0, float(elevation)), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))

    polyline = create_ifcpolyline(f, wall.extrusion_area())
    profile = f.createIfcArbitraryClosedProfileDef("AREA", None, polyline)

    solid = create_ifcextrudedareasolid(f, profile, extrusion_placement, (0.0, 0.0, 1.0), wall.height)
    body = f.createIfcShapeRepresentation(ifc_store.get_context("Body"), "Body", "SweptSolid", [solid])

    product_shape = f.createIfcProductDefinitionShape(None, None, [axis_representation, body])

    wall_el = f.create_entity(
        "IfcWall",
        GlobalId=wall.guid,
        OwnerHistory=owner_history,
        Name=wall.name,
        Description="An awesome wall",
        ObjectType=None,
        ObjectPlacement=wall_placement,
        Representation=product_shape,
    )

    return wall_el


def add_ifc_insert_elem(wall: Wall, shape_, opening_element, wall_el, ifc_type):
    a = wall.parent.get_assembly()
    ifc_store = a.ifc_store
    f = ifc_store.f

    owner_history = ifc_store.owner_history
    schema = f.wrapped_data.schema

    # Create a simplified representation for the Window
    insert_placement = create_local_placement(f, O, Z, X, wall_el.ObjectPlacement)

    shape = shape_.solid_occ()

    insert_shape_ = tesselate_shape(shape, schema, Units.get_general_point_tol(a.units))
    insert_shape = f.add(insert_shape_)

    # Link to representation context
    body_context = ifc_store.get_context("Body")
    for rep in insert_shape.Representations:
        rep.ContextOfItems = body_context

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
