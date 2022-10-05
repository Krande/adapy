from __future__ import annotations

import logging

from ..utils import create_guid, create_property_set


def copy_deep(ifc_file, element):
    import ifcopenshell.util.element

    new = ifc_file.create_entity(element.is_a())

    for i, attribute in enumerate(element):
        if attribute is None:
            continue
        if isinstance(attribute, ifcopenshell.entity_instance):
            attribute = copy_deep(ifc_file, attribute)
        elif isinstance(attribute, tuple) and attribute and isinstance(attribute[0], ifcopenshell.entity_instance):
            attribute = list(attribute)
            for j, item in enumerate(attribute):
                attribute[j] = copy_deep(ifc_file, item)
        try:
            new[i] = attribute
        except TypeError as e:
            # Handle invalid IFC element created by a certain proprietary CAD software
            if i != 4 and element.is_a() == "IfcTriangulatedFaceSet":
                raise TypeError(e)
            logging.debug(f'Handling invalid property created by proprietary software:\n"{e}"')
            new[i] = None

    # Add properties
    if hasattr(new, "OwnerHistory") is False:
        return new

    if hasattr(element, "IsDefinedBy") and len(element.IsDefinedBy) > 0:
        owner_history = new.OwnerHistory
        for key, pset in ifcopenshell.util.element.get_psets(element).items():
            props = create_property_set(key, ifc_file, pset, owner_history=owner_history)
            ifc_file.create_entity(
                "IfcRelDefinesByProperties",
                create_guid(),
                owner_history,
                key,
                None,
                [new],
                props,
            )

    return new
