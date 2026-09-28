from __future__ import annotations

from ifcopenshell import file as ifile

from ada import Beam
from ada.cadit.ifc.utils import add_colour, create_object_placement
from ada.cadit.ifc.write.geom.points import cpt
from ada.cadit.ifc.write.geom.solids import extruded_area_solid


def extrude_straight_beam(beam: Beam, f: ifile, profile):
    parent = f.by_guid(beam.parent.guid)
    a = beam.parent.get_assembly()
    body_context = a.ifc_store.get_context("Body")

    # The body is in the local system of the beam, placed by the beam placement relative to its parent
    solid = extruded_area_solid(beam.solid_geom().geometry, f, profile)
    body = f.createIfcShapeRepresentation(body_context, "Body", "SolidModel", [solid])
    loc_plac = create_object_placement(f, beam, relative_to=parent.ObjectPlacement)

    axis_context = a.ifc_store.get_context("Axis")
    ifc_polyline = f.create_entity("IfcPolyLine", [cpt(f, beam.n1.p), cpt(f, beam.n2.p)])
    axis = f.create_entity("IfcShapeRepresentation", axis_context, "Axis", "Curve3D", [ifc_polyline])

    # Add colour
    if beam.color is not None:
        add_colour(f, solid, str(beam.color), beam.color)

    return body, axis, loc_plac
