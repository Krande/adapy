from __future__ import annotations

from typing import TYPE_CHECKING

from ada.cadit.ifc.utils import (
    create_axis,
    create_ifc_placement,
    create_local_placement,
)
from ada.cadit.ifc.write.geom.points import cpt
from ada.core.guid import create_guid
from ada.core.utils import to_real
from ada.core.vector_utils import unit_vector, vector_length

if TYPE_CHECKING:
    from ada import Assembly
    from ada.api.primitives import (
        BSplineSurfaceWithKnots,
        RationalBSplineSurfaceWithKnots,
    )
    from ada.cadit.ifc.store import IfcStore


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


def add_bsplinesurface_to_ifc(
    surface: BSplineSurfaceWithKnots | RationalBSplineSurfaceWithKnots, assembly: Assembly, parent_guid: str = None
):
    """A temporary function to add a B-Spline surface to an IFC file. To be integrated into ifcstore.sync()"""
    f = assembly.ifc_store.f
    context = assembly.ifc_store.get_context("Body")
    ifc_surface = surface.to_ifcopenshell(f)

    # IfcPolyLine
    p11 = cpt(f, (0, 0, 0))
    p21 = cpt(f, (0, 0, 1))
    poly_line = f.create_entity("IFCPOLYLINE", (p11, p21))

    # List of vertex points
    p1 = cpt(f, (0, 0, 0))
    p2 = cpt(f, (0, 0, 1))
    vp1 = f.create_entity("IfcVertexPoint", p1)
    vp2 = f.create_entity("IfcVertexPoint", p2)

    # List of edge curves
    edge_curve_1 = f.create_entity("IFCEDGECURVE", vp1, vp2, poly_line, True)

    # List of orient edges
    orient_edge_1 = f.create_entity("IFCORIENTEDEDGE", None, None, edge_curve_1, True)

    edge_loop = f.create_entity("IFCEDGELOOP", (orient_edge_1,))
    outer_bound = f.create_entity("IFCFACEOUTERBOUND", edge_loop, True)
    advanced_face = f.create_entity("IFCADVANCEDFACE", (outer_bound,), ifc_surface, True)
    closed_shell = f.create_entity("IFCCLOSEDSHELL", (advanced_face,))
    advanced_brep = f.create_entity("IFCADVANCEDBREP", closed_shell)
    shape_rep = f.create_entity("IFCSHAPEREPRESENTATION", context, "Body", "AdvancedBrep", (advanced_brep,))
    prod_def_shape = f.create_entity("IFCPRODUCTDEFINITIONSHAPE", None, None, (shape_rep,))
    local_place = create_local_placement(f)
    bldg_el_proxy = f.create_entity(
        "IFCBUILDINGELEMENTPROXY",
        create_guid(),
        assembly.ifc_store.owner_history,
        "BuildingElementProxy",
        None,
        None,
        local_place,
        prod_def_shape,
        None,
        "NOTDEFINED",
    )
    assembly.ifc_store.writer.add_related_elements_to_spatial_container([bldg_el_proxy], parent_guid)
