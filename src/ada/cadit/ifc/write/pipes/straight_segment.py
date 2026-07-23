from __future__ import annotations

from typing import TYPE_CHECKING

from ada import PipeSegStraight
from ada.cadit.ifc.utils import add_colour, create_ifcpolyline, create_local_placement
from ada.cadit.ifc.write.geom import solids as igeo_so
from ada.cadit.ifc.write.pipes.entity_class import segment_entity_class
from ada.core.utils import to_real

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore


def write_pipe_straight_seg(ifc_store: IfcStore, pipe_seg: PipeSegStraight):  #  -> ifcopenshell.entity_instance
    if pipe_seg.parent is None:
        raise ValueError("Parent cannot be None for IFC export")

    f = ifc_store.f
    owner_history = ifc_store.owner_history

    p1 = pipe_seg.p1
    p2 = pipe_seg.p2

    rp1 = to_real(p1.p)
    rp2 = to_real(p2.p)

    solid_geo = pipe_seg.solid_geom()
    # Reuse the section's parametric profile (carries the ADA parameter bag — e.g. an
    # IfcCircleHollowProfileDef) for the body, like beams do, so the section round-trips
    # exactly instead of an inline bag-less IfcArbitraryProfileDefWithVoids.
    profile = ifc_store.get_profile_def(pipe_seg.section)
    solid = igeo_so.extruded_area_solid(solid_geo.geometry, f, profile)

    if solid_geo.color is not None:
        add_colour(f, solid, str(solid_geo.color), solid_geo.color)

    polyline = create_ifcpolyline(f, [rp1, rp2])

    axis_representation = f.createIfcShapeRepresentation(
        ifc_store.get_context("Axis"),
        "Axis",
        "Curve3D",
        [polyline],
    )

    body_representation = f.createIfcShapeRepresentation(
        ifc_store.get_context("Body"),
        "Body",
        "SweptSolid",
        [solid],
    )

    product_shape = f.createIfcProductDefinitionShape(
        None,
        None,
        [axis_representation, body_representation],
    )

    local_placement = create_local_placement(f)

    pipe_segment = f.create_entity(
        segment_entity_class(pipe_seg),
        GlobalId=pipe_seg.guid,
        OwnerHistory=owner_history,
        Name=pipe_seg.name,
        Description="Pipe segment",
        ObjectType=None,
        ObjectPlacement=local_placement,
        Representation=product_shape,
        Tag=None,
    )

    return pipe_segment
