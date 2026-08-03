from __future__ import annotations

from ifcopenshell import file as ifile

from ada.api.beams import BeamCurved
from ada.cadit.ifc.utils import add_colour, create_local_placement
from ada.cadit.ifc.write.geom.points import cpt
from ada.cadit.ifc.write.geom.solids import fixed_reference_swept_area_solid


def create_curved_beam(beam: BeamCurved, f: ifile, profile):
    """Write a ``BeamCurved`` (its section swept along an analytic 3D directrix) as
    an ``IfcFixedReferenceSweptAreaSolid`` — the same solid a routed duct/cable-tray
    run carries. The section profile comes from the swept solid itself (so a rotated
    open-tray profile is preserved); ``profile`` is unused but kept for a uniform
    beam-writer signature."""
    parent = f.by_guid(beam.parent.guid)
    a = beam.parent.get_assembly()
    body_context = a.ifc_store.get_context("Body")
    global_placement = create_local_placement(f, relative_to=parent.ObjectPlacement)

    solid = fixed_reference_swept_area_solid(beam.solid_geom().geometry, f)
    body = f.create_entity("IfcShapeRepresentation", body_context, "Body", "SweptSolid", [solid])
    loc_plac = create_local_placement(f, relative_to=global_placement)

    axis_context = a.ifc_store.get_context("Axis")
    ifc_polyline = f.create_entity("IfcPolyLine", [cpt(f, beam.n1.p), cpt(f, beam.n2.p)])
    axis = f.create_entity("IfcShapeRepresentation", axis_context, "Axis", "Curve3D", [ifc_polyline])

    if beam.color is not None:
        add_colour(f, solid, str(beam.color), beam.color)

    return axis, body, loc_plac
