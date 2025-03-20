from __future__ import annotations

import numpy as np
from ifcopenshell import file as ifile

import ada.cadit.ifc.write.geom.solids as igeo_so
from ada import CurveRevolve
from ada.api.beams import BeamRevolve
from ada.cadit.ifc.utils import create_local_placement, create_ifc_placement
from ada.cadit.ifc.write.geom.points import cpt


def create_revolved_beam(beam: BeamRevolve, f: "ifile", profile):
    a = beam.parent.get_assembly()

    body_context = a.ifc_store.get_context("Body")
    axis_context = a.ifc_store.get_context("Axis")

    curve: CurveRevolve = beam.curve

    ifc_trim_curve = create_ifc_trimmed_curve(curve, f)
    placement = create_local_placement(f, curve.p1, (0, 0, 1))
    solid_geom = beam.solid_geom()

    rev_area_solid = igeo_so.revolved_area_solid(solid_geom.geometry, f)

    axis = f.create_entity("IfcShapeRepresentation", axis_context, "Axis", "Curve3D", [ifc_trim_curve])
    body = f.create_entity("IfcShapeRepresentation", body_context, "Body", "SweptSolid", [rev_area_solid])

    return body, axis, placement


def create_ifc_trimmed_curve(curve: CurveRevolve, f: "ifile"):
    loc_plac = create_ifc_placement(f, origin=curve.rot_origin)
    ifc_circle = f.create_entity("IFCCIRCLE", loc_plac, curve.radius)
    param1 = (f.create_entity("IFCPARAMETERVALUE", 0.0), cpt(f, curve.p1))
    param2 = (f.create_entity("IFCPARAMETERVALUE", np.deg2rad(curve.angle)), cpt(f, curve.p2))
    trim_curve = f.create_entity(
        "IFCTRIMMEDCURVE",
        BasisCurve=ifc_circle,
        Trim1=param1,
        Trim2=param2,
        SenseAgreement=True,
        MasterRepresentation="PARAMETER",
    )
    return trim_curve


