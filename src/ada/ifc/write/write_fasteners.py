from __future__ import annotations

from typing import TYPE_CHECKING

from ada.core.vector_utils import unit_vector, vector_length
from ada.ifc.utils import create_axis, create_ifc_placement, to_real

if TYPE_CHECKING:
    import ifcopenshell

    from ada.concepts.fasteners import Weld


# https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/lexical/IfcFastener.htm
# https://standards.buildingsmart.org/IFC/RELEASE/IFC4_3/lexical/Pset_FastenerWeld.htm


def write_ifc_fastener(weld: Weld) -> ifcopenshell.entity_instance:
    if weld.parent is None:
        raise ValueError("Parent cannot be None for IFC export")

    a = weld.parent.get_assembly()
    ifc_store = a.ifc_store
    f = a.ifc_store.f

    geom = write_ifc_fastener_geometry(weld)

    return f.create_entity(
        "IfcFastener",
        GlobalId=weld.guid,
        OwnerHistory=ifc_store.owner_history,
        Name=weld.name,
        Description=None,
        Representation=geom,
        PredefinedType="WELD",
    )


def write_ifc_fastener_geometry(weld: Weld):
    a = weld.parent.get_assembly()
    ifc_store = a.ifc_store
    f = a.ifc_store.f
    context = ifc_store.get_context()
    points = [to_real(x.p) for x in weld.points]
    axis_representation = create_axis(f, points, context)

    section_profile = ifc_store.get_profile_def(weld.section)
    if section_profile is None:
        raise ValueError("Section profile not found")

    body = create_extruded_body(f, *weld.points, section_profile)

    body_representation = f.createIfcShapeRepresentation(context, "Body", "SweptSolid", [body])
    return f.create_entity(
        "IfcProductDefinitionShape",
        Name=None,
        Description=None,
        Representations=[axis_representation, body_representation],
    )


def create_extruded_body(f, p1, p2, section_profile):
    xdir = to_real(unit_vector(p2.p - p1.p))
    ifcdir = f.createIfcDirection(xdir)
    extrusion_placement = create_ifc_placement(f, (0.0, 0.0, 0.0), (0.0, 0.0, 1.0), (1.0, 0.0, 0.0))
    seg_l = vector_length(p2.p - p1.p)

    return f.createIfcExtrudedAreaSolid(section_profile, extrusion_placement, ifcdir, seg_l)
