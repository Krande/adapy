from __future__ import annotations

from typing import TYPE_CHECKING

from ada.core.vector_utils import unit_vector, vector_length
from ada.ifc.utils import create_axis, create_ifc_placement, to_real

if TYPE_CHECKING:
    from ada.ifc.store import IfcStore


def generate_extruded_area_solid_prod_def(ifc_store: IfcStore, p_start, p_end, section):
    f = ifc_store.f

    body_context = ifc_store.get_context("Body")
    axis_context = ifc_store.get_context("Axis")

    axis_representation = create_axis(f, [p_start, p_end], axis_context)

    section_profile = ifc_store.get_profile_def(section)
    if section_profile is None:
        raise ValueError("Section profile not found")

    body = create_extruded_body(f, p_start, p_end, section_profile)

    body_representation = f.createIfcShapeRepresentation(body_context, "Body", "SweptSolid", [body])
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
