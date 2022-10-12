from __future__ import annotations

from typing import TYPE_CHECKING

from ada.core.constants import O, X, Z
from ada.core.utils import Counter
from ada.ifc.utils import create_guid, create_local_placement, write_elem_property_sets

from .write_shapes import generate_parametric_solid

if TYPE_CHECKING:
    from ada import Penetration

pen_counter = Counter(prefix="P")


def generate_ifc_opening(penetration: Penetration):
    if penetration.parent is None:
        raise ValueError("This penetration has no parent")
    pen_name = f"{penetration.name}_{next(pen_counter)}"
    a = penetration.get_assembly()
    parent_part = penetration.parent.parent
    f = a.ifc_store.f

    geom_parent = f.by_guid(parent_part.guid)
    owner_history = a.ifc_store.owner_history

    # Create and associate an opening for the window in the wall
    opening_placement = create_local_placement(f, O, Z, X, geom_parent.ObjectPlacement)
    opening_shape = generate_parametric_solid(penetration.primitive, f)

    opening_element = f.create_entity(
        "IfcOpeningElement",
        GlobalId=create_guid(),
        OwnerHistory=owner_history,
        Name=pen_name,
        Description=pen_name + " (Opening)",
        ObjectPlacement=opening_placement,
        Representation=opening_shape,
    )

    write_elem_property_sets(penetration.metadata, opening_element, f, owner_history)

    return opening_element
