from __future__ import annotations

import numpy as np
from ifcopenshell import file as ifile

from ada.api.beams import BeamTapered
from ada.cadit.ifc.utils import add_colour, create_local_placement, ifc_dir
from ada.cadit.ifc.write.geom.solids import extruded_area_solid_tapered
from ada.config import Config
from ada.core.utils import to_real


def extrude_straight_tapered_beam(beam: BeamTapered, f: ifile, profile):
    """Extrude a straight beam with a tapered profile"""
    parent = f.by_guid(beam.parent.guid)
    a = beam.parent.get_assembly()

    global_placement = create_local_placement(f, relative_to=parent.ObjectPlacement)

    e1 = (0.0, 0.0, 0.0)

    vec = beam.xvec
    yvec = beam.yvec
    if Config().ifc_export_include_ecc and beam.e1 is not None:
        e1 = beam.e1
        vec = beam.xvec_e

    # Transform coordinates to local coords
    p1 = tuple([float(x) + float(e1[i]) for i, x in enumerate(beam.n1.p.copy())])
    p2 = p1 + np.array([0, 0, 1]) * beam.length

    p1_ifc = f.create_entity("IfcCartesianPoint", to_real(p1))
    p2_ifc = f.create_entity("IfcCartesianPoint", to_real(p2))

    ifc_polyline = f.create_entity("IfcPolyLine", [p1_ifc, p2_ifc])

    solid = extruded_area_solid_tapered(beam.solid_geom().geometry, f)

    # Add colour
    if beam.color is not None:
        add_colour(f, solid, str(beam.color), beam.color)

    body_context = a.ifc_store.get_context("Body")
    axis_context = a.ifc_store.get_context("Axis")
    ax23d = f.create_entity("IfcAxis2Placement3D", p1_ifc, ifc_dir(f, vec), ifc_dir(f, yvec))
    loc_plac = f.create_entity("IfcLocalPlacement", global_placement, ax23d)
    body = f.create_entity("IfcShapeRepresentation", body_context, "Body", "SweptSolid", [solid])
    axis = f.create_entity("IfcShapeRepresentation", axis_context, "Axis", "Curve3D", [ifc_polyline])

    return body, axis, loc_plac
