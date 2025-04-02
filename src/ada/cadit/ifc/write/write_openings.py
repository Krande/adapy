from __future__ import annotations

from typing import TYPE_CHECKING

from ada.cadit.ifc.utils import create_local_placement, write_elem_property_sets
from ada.cadit.ifc.write.write_shapes import generate_parametric_solid
from ada.core.constants import O, X, Z
from ada.core.guid import create_guid
from ada.core.utils import Counter

if TYPE_CHECKING:
    from ada import Boolean, Shape

pen_counter = Counter(prefix="P")


def generate_ifc_opening(primitive: Boolean | Shape):
    from ada import Boolean, Shape

    if primitive.parent is None:
        raise ValueError("This penetration has no parent")
    pen_name = f"{primitive.name}_{next(pen_counter)}"
    a = primitive.get_assembly()
    parent_part = primitive.parent.parent
    f = a.ifc_store.f

    geom_parent = f.by_guid(parent_part.guid)
    owner_history = a.ifc_store.owner_history

    # Create and associate an opening for the window in the wall
    opening_placement = create_local_placement(f, O, Z, X, relative_to=geom_parent.ObjectPlacement)
    if isinstance(primitive, Boolean):
        prim = primitive.primitive
    elif issubclass(type(primitive), Shape):
        prim = primitive
    else:
        raise NotImplementedError()
    opening_shape = generate_parametric_solid(prim, f)

    opening_element = f.create_entity(
        "IfcOpeningElement",
        GlobalId=create_guid(),
        OwnerHistory=owner_history,
        Name=pen_name,
        Description=pen_name + " (Opening)",
        ObjectPlacement=opening_placement,
        Representation=opening_shape,
    )

    write_elem_property_sets(primitive.metadata, opening_element, f, owner_history)

    return opening_element
