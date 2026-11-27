"""Which IFC property sets an object's metadata is written as -- the one rule both IFC writers use.

Kept free of ifcopenshell so the native writer (adacpp) states an object's property sets exactly as
the ifcopenshell writer does: a reader then finds the same sets whichever writer wrote the file.
"""

from __future__ import annotations

#: The set an object's flat (non-dict) metadata values are written to.
DEFAULT_PROPERTY_SET = "Properties"


def element_property_sets(metadata_props: dict | None) -> dict[str, dict]:
    """``{set name: {property: value}}`` for an object's metadata, or ``{}`` when nothing is written.

    Metadata may be homogeneous (every value a nested property-set dict), flat (all scalar/list
    values -> one "Properties" set), or MIXED (nested dicts alongside scalar tags): each dict value
    is its own named set, and the remaining scalar/list values go to one default set. Writer
    instructions (the IFC entity choice, node relations) are not properties and are left out.
    """
    from ada.base.ifc_types import IFC_ENTITY_KEY
    from ada.cadit.ifc.write.write_relations import RELATIONS_KEY
    from ada.config import Config

    metadata_props = metadata_props or {}
    if IFC_ENTITY_KEY in metadata_props or RELATIONS_KEY in metadata_props:
        metadata_props = {k: v for k, v in metadata_props.items() if k not in (IFC_ENTITY_KEY, RELATIONS_KEY)}
    if len(metadata_props.keys()) == 0 or Config().ifc_export_props is False:
        return {}

    nested = {k: v for k, v in metadata_props.items() if isinstance(v, dict)}
    if not nested:
        return {DEFAULT_PROPERTY_SET: dict(metadata_props)}
    out = {str(k): v for k, v in nested.items()}
    flat = {k: v for k, v in metadata_props.items() if not isinstance(v, dict)}
    if flat:
        out[DEFAULT_PROPERTY_SET] = flat
    return out
