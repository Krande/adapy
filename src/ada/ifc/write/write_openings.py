from typing import TYPE_CHECKING

from ada.core.constants import O, X, Z
from ada.ifc.utils import (
    add_multiple_props_to_elem,
    create_guid,
    create_local_placement,
)

from .write_shapes import generate_parametric_solid

if TYPE_CHECKING:
    from ada import Penetration


def generate_ifc_opening(penetration: "Penetration"):
    if penetration.parent is None:
        raise ValueError("This penetration has no parent")

    a = penetration.parent.parent.get_assembly()
    f = a.ifc_file

    geom_parent = penetration.parent.parent.get_ifc_elem()
    owner_history = a.user.to_ifc()

    # Create and associate an opening for the window in the wall
    opening_placement = create_local_placement(f, O, Z, X, geom_parent.ObjectPlacement)
    opening_shape = generate_parametric_solid(penetration.primitive, f)

    opening_element = f.createIfcOpeningElement(
        create_guid(),
        owner_history,
        penetration.name,
        penetration.name + " (Opening)",
        None,
        opening_placement,
        opening_shape,
        None,
    )

    add_multiple_props_to_elem(penetration.metadata.get("props", dict()), opening_element, f, owner_history)

    return opening_element
