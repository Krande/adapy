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
    from ada.api.curves import CurveOpen2d

    if not isinstance(beam.curve, CurveOpen2d):
        return _create_swept_beam_from_directrix(beam, f, profile)

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


def _create_swept_beam_from_directrix(beam: BeamSweep, f, profile):
    """A sweep path given as 3D points or ``ada.geom`` lines/arcs: an
    ``IfcFixedReferenceSweptAreaSolid`` over the path in object coordinates (identity
    ``Position``), its ``FixedReference`` the section's local y at the start -- for a path in one
    plane that holds the section as the rotation-minimising frame does."""
    from ada.cadit.ifc.write.geom.curves import indexed_poly_curve
    from ada.geom import curves as geo_cu

    a = beam.parent.get_assembly()
    body_context = a.ifc_store.get_context("Body")
    axis_context = a.ifc_store.get_context("Axis")

    directrix = beam.directrix
    if not isinstance(directrix, geo_cu.IndexedPolyCurve):
        directrix = geo_cu.IndexedPolyCurve([directrix])
    ifc_curve = indexed_poly_curve(directrix, f)

    _, dir_x, _ = beam.sweep_frames()
    solid = f.create_entity(
        "IfcFixedReferenceSweptAreaSolid",
        SweptArea=profile,
        Position=create_ifc_placement(f),
        Directrix=ifc_curve,
        FixedReference=ifc_dir(f, tuple(float(c) for c in dir_x[0])),
    )
    axis = f.create_entity("IfcShapeRepresentation", axis_context, "Axis", "Curve3D", [ifc_curve])
    body = f.create_entity("IfcShapeRepresentation", body_context, "Body", "SweptSolid", [solid])
    if beam.color is not None:
        add_colour(f, solid, str(beam.color), beam.color)

    return axis, body, create_local_placement(f)


def sweep_beam(beam, f, profile, global_placement, extrude_dir):
    ifc_polyline = write_curve_poly(beam.curve)

    extrude_area_solid = f.create_entity(
        "IfcFixedReferenceSweptAreaSolid", profile, global_placement, ifc_polyline, 0.0, 1.0, extrude_dir
    )
    loc_plac = create_ifc_placement(f)
    return extrude_area_solid, loc_plac, ifc_polyline
