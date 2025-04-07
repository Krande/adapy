from __future__ import annotations

from typing import TYPE_CHECKING

from ada.cadit.ifc.utils import create_axis, create_property_set
from ada.cadit.ifc.write.shapes.prim_extrude_area import generate_ifc_prim_extrude_geom

if TYPE_CHECKING:
    import ifcopenshell

    from ada.api.fasteners import Weld


# https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/lexical/IfcFastener.htm
# https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/lexical/Pset_FastenerWeld.htm


def write_ifc_fastener(weld: Weld) -> ifcopenshell.entity_instance:
    if weld.parent is None:
        raise ValueError("Parent cannot be None for IFC export")

    a = weld.parent.get_assembly()
    ifc_store = a.ifc_store
    f = a.ifc_store.f

    axis = create_axis(f, [weld.p1.p, weld.p2.p], ifc_store.get_context("Axis"))
    geom = generate_ifc_prim_extrude_geom(weld.geometry, f)
    body = f.createIfcShapeRepresentation(ifc_store.get_context("Body"), "Body", "SweptSolid", [geom])
    shape = f.create_entity("IfcProductDefinitionShape", Name=None, Description=None, Representations=[axis, body])

    ifc_fastener = f.create_entity(
        "IfcFastener",
        GlobalId=weld.guid,
        OwnerHistory=ifc_store.owner_history,
        Name=weld.name,
        Description=None,
        Representation=shape,
        PredefinedType="WELD",
    )

    # https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/lexical/Pset_FastenerWeld.htm
    create_property_set("Pset_FastenerWeld", f, dict(Type1=weld.type.value), ifc_store.owner_history)

    return ifc_fastener
