from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from ifcopenshell.util.placement import get_local_placement

from ada import Beam, Placement
from ada.api.beams import BeamRevolve
from ada.api.curves import CurveRevolve
from ada.config import logger
from ada.core.vector_utils import calc_yvec, unit_vector

from .geom.geom_reader import get_product_definitions
from .geom.placement import axis3d
from .read_beam_section import import_section_from_ifc
from .read_materials import read_material
from .reader_utils import (
    get_associated_material,
    get_ifc_body,
    get_placement,
    get_point,
    get_swept_area,
)

if TYPE_CHECKING:
    from ada.cadit.ifc.store import IfcStore


def import_ifc_beam(ifc_elem, name, ifc_store: IfcStore) -> Beam:
    from .exceptions import NoIfcAxesAttachedError

    mat_ref = get_associated_material(ifc_elem)

    mat_name = mat_ref.Material.Name if hasattr(mat_ref, "Material") else mat_ref.Name
    mat = ifc_store.assembly.get_by_name(mat_name)
    if mat is None:
        mat = read_material(mat_ref, ifc_store)

    swept_area = get_swept_area(ifc_elem)
    sec = import_section_from_ifc(swept_area, units=ifc_store.assembly.units)

    axes = [rep for rep in ifc_elem.Representation.Representations if rep.RepresentationIdentifier == "Axis"]

    if len(axes) != 1:
        raise NoIfcAxesAttachedError("Number of axis objects attached to IfcBeam is not 1")
    if len(axes[0].Items) != 1:
        raise ValueError("Number of items objects attached to axis is not 1")

    axis = axes[0].Items[0]

    # Dispatch on the body solid type — the authoritative discriminator. (The axis curve type
    # is unreliable: a swept beam's axis is an IfcIndexedPolyCurve, not an IfcPolyline.)
    body_solid = get_ifc_body(ifc_elem)
    body_type = body_solid.is_a()
    if body_type == "IfcRevolvedAreaSolid":
        return import_revolved_beam(ifc_elem, axis, name, sec, mat, ifc_store)
    elif body_type == "IfcFixedReferenceSweptAreaSolid":
        return import_polyline_beam(ifc_elem, axis, name, sec, mat, ifc_store)
    else:
        # IfcExtrudedAreaSolid (straight) or IfcExtrudedAreaSolidTapered (→ BeamTapered)
        return import_straight_beam(ifc_elem, axis, name, sec, mat, ifc_store)


def import_straight_beam(ifc_elem, axis, name, sec, mat, ifc_store: IfcStore) -> Beam:
    bodies = get_product_definitions(ifc_elem)
    if len(bodies) != 1:
        raise ValueError("Number of body objects attached to element is not 1")
    body = bodies[0]

    rel_place = Placement.from_axis3d(axis3d(ifc_elem.ObjectPlacement.RelativePlacement))

    extrude_dir = unit_vector(rel_place.transform_vector(body.position.axis, inverse=True))
    ref_dir = unit_vector(rel_place.transform_vector(body.position.ref_direction))
    p1 = body.position.location
    local_y = calc_yvec(ref_dir, extrude_dir)
    p2 = p1 + extrude_dir * body.depth

    extra_opts = {}
    obj_placement = ifc_elem.ObjectPlacement
    if obj_placement.PlacementRelTo:
        local_placement = get_local_placement(obj_placement)
        place = Placement.from_4x4_matrix(local_placement)
        extra_opts["placement"] = place

    common = dict(
        sec=sec,
        mat=mat,
        up=local_y,
        guid=ifc_elem.GlobalId,
        ifc_store=ifc_store,
        units=ifc_store.assembly.units,
        **extra_opts,
    )

    # A tapered extrusion (IfcExtrudedAreaSolidTapered) carries an EndSweptArea — reconstruct
    # the BeamTapered subtype with its end section rather than collapsing to a prismatic Beam.
    body_solid = get_ifc_body(ifc_elem)
    if body_solid.is_a("IfcExtrudedAreaSolidTapered"):
        from ada import BeamTapered

        end_sec = import_section_from_ifc(body_solid.EndSweptArea, units=ifc_store.assembly.units)
        return BeamTapered(name, p1, p2, tap=end_sec, **common)

    return Beam(name, p1, p2, **common)


def import_revolved_beam(ifc_elem, axis, name, sec, mat, ifc_store: IfcStore) -> Beam:
    from ada import Placement
    from ada.core.vector_transforms import transform3d

    logger.debug("Reading revolved IFC beam (swept along IfcTrimmedCurve)")

    r = axis.BasisCurve.Radius
    curve_place = get_placement(axis.BasisCurve.Position)
    beam_place = get_placement(ifc_elem.ObjectPlacement.RelativePlacement)
    p1 = get_point(axis.Trim1[1])
    p2 = get_point(axis.Trim2[1])
    global_place = Placement()
    angle = axis.Trim2[0].wrappedValue
    rot_origin = transform3d(beam_place.rot_matrix, global_place.rot_matrix, global_place.origin, [curve_place.origin])[
        0
    ]
    rot_axis = transform3d(curve_place.rot_matrix, global_place.rot_matrix, global_place.origin, [curve_place.zdir])[0]

    p1g, p2g = transform3d(beam_place.rot_matrix, global_place.rot_matrix, beam_place.origin, [p1, p2])

    curve = CurveRevolve(p1g, p2g, radius=r, rot_axis=rot_axis, rot_origin=rot_origin, angle=np.rad2deg(angle))

    return BeamRevolve(
        name, curve=curve, sec=sec, mat=mat, guid=ifc_elem.GlobalId, ifc_store=ifc_store, units=ifc_store.assembly.units
    )


def import_polyline_beam(ifc_elem, axis, name, sec, mat, ifc_store: IfcStore) -> Beam:
    """A beam swept along a multi-point polyline directrix (IfcFixedReferenceSweptAreaSolid) →
    BeamSweep. The Axis polyline is the sweep curve (written from beam.curve)."""
    from ada import BeamSweep
    from ada.api.curves import CurvePoly2d

    # The sweep directrix is written by write_curve_poly as an IfcIndexedPolyCurve (points in
    # a CartesianPointList), not an IfcPolyline (list of CartesianPoints) — handle both.
    if axis.is_a("IfcIndexedPolyCurve"):
        coords = [tuple(float(c) for c in pt) for pt in axis.Points.CoordList]
    else:
        coords = [tuple(float(c) for c in p.Coordinates) for p in axis.Points]
    curve = CurvePoly2d.from_3d_points(coords)

    return BeamSweep(
        name,
        curve=curve,
        sec=sec,
        mat=mat,
        guid=ifc_elem.GlobalId,
        ifc_store=ifc_store,
        units=ifc_store.assembly.units,
    )
