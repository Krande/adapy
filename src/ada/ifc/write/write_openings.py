from typing import TYPE_CHECKING

from ada.core.constants import O, X, Z
from ada.ifc.utils import create_guid, create_local_placement, write_elem_property_sets

from .write_shapes import generate_parametric_solid

if TYPE_CHECKING:
    from ada import Penetration


def generate_ifc_opening(penetration: "Penetration"):
    if penetration.parent is None:
        raise ValueError("This penetration has no parent")

    a = penetration.parent.parent.get_assembly()
    f = a.ifc_store.f

    geom_parent = penetration.parent.parent.get_ifc_elem()
    owner_history = a.ifc_store.owner_history

    # Create and associate an opening for the window in the wall
    opening_placement = create_local_placement(f, O, Z, X, geom_parent.ObjectPlacement)
    opening_shape = generate_parametric_solid(penetration.primitive, f)

    opening_element = f.create_entity(
        "IfcOpeningElement",
        create_guid(),
        owner_history,
        penetration.name,
        penetration.name + " (Opening)",
        None,
        opening_placement,
        opening_shape,
        None,
    )

    write_elem_property_sets(penetration.metadata.get("props", dict()), opening_element, f, owner_history)

    return opening_element
