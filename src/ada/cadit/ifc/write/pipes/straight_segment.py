from __future__ import annotations

from ada import PipeSegStraight
from ada.cadit.ifc.utils import add_colour, create_ifcpolyline, create_local_placement
from ada.cadit.ifc.write.geom import solids as igeo_so
from ada.core.utils import to_real


def write_pipe_straight_seg(pipe_seg: PipeSegStraight):
    if pipe_seg.parent is None:
        raise ValueError("Parent cannot be None for IFC export")

    assembly = pipe_seg.parent.get_assembly()
    ifc_store = assembly.ifc_store
    f = ifc_store.f
    owner_history = ifc_store.owner_history

    p1 = pipe_seg.p1
    p2 = pipe_seg.p2

    rp1 = to_real(p1.p)
    rp2 = to_real(p2.p)

    solid_geo = pipe_seg.solid_geom()
    solid = igeo_so.extruded_area_solid(solid_geo.geometry, f)

    if solid_geo.color is not None:
        add_colour(f, solid, str(solid_geo.color), solid_geo.color)

    polyline = create_ifcpolyline(f, [rp1, rp2])

    axis_representation = f.createIfcShapeRepresentation(ifc_store.get_context("Axis"), "Axis", "Curve3D", [polyline])
    body_representation = f.createIfcShapeRepresentation(ifc_store.get_context("Body"), "Body", "SweptSolid", [solid])

    product_shape = f.createIfcProductDefinitionShape(None, None, [axis_representation, body_representation])

    local_placement = create_local_placement(f)

    pipe_segment = f.create_entity(
        "IfcPipeSegment",
        GlobalId=pipe_seg.guid,
        OwnerHistory=owner_history,
        Name=pipe_seg.name,
        Description="An awesome pipe",
        ObjectType=None,
        ObjectPlacement=local_placement,
        Representation=product_shape,
        Tag=None,
    )

    return pipe_segment
