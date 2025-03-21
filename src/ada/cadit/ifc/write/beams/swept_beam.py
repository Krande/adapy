from __future__ import annotations

from ada.api.beams import BeamSweep
from ada.cadit.ifc.utils import (
    add_colour,
    create_ifc_placement,
    create_local_placement,
    ifc_dir,
)
from ada.cadit.ifc.write.write_curves import write_curve_poly


def create_swept_beam(beam: BeamSweep, f, profile):
    a = beam.parent.get_assembly()
    body_context = a.ifc_store.get_context("Body")
    axis_context = a.ifc_store.get_context("Axis")

    ifc_polyline = write_curve_poly(beam.curve)

    extrude_dir = ifc_dir(f, (0.0, 0.0, 1.0))

    placement = create_local_placement(f)
    place = create_ifc_placement(f, beam.n1.p, beam.curve.normal)
    extrude_area_solid = f.create_entity(
        "IfcFixedReferenceSweptAreaSolid", profile, place, ifc_polyline, FixedReference=extrude_dir
    )

    axis = f.create_entity("IfcShapeRepresentation", axis_context, "Axis", "Curve3D", [ifc_polyline])
    body = f.create_entity("IfcShapeRepresentation", body_context, "Body", "SweptSolid", [extrude_area_solid])

    # Add colour
    if beam.color is not None:
        add_colour(f, extrude_area_solid, str(beam.color), beam.color)

    return axis, body, placement


def sweep_beam(beam, f, profile, global_placement, extrude_dir):
    ifc_polyline = write_curve_poly(beam.curve)

    extrude_area_solid = f.create_entity(
        "IfcFixedReferenceSweptAreaSolid", profile, global_placement, ifc_polyline, 0.0, 1.0, extrude_dir
    )
    loc_plac = create_ifc_placement(f)
    return extrude_area_solid, loc_plac, ifc_polyline
