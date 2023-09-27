from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

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
    if axis.is_a("IfcPolyline") and len(axis.Points) != 2:
        return import_polyline_beam(ifc_elem, axis, name, sec, mat, ifc_store)
    elif axis.is_a("IfcTrimmedCurve"):
        return import_revolved_beam(ifc_elem, axis, name, sec, mat, ifc_store)
    else:
        return import_straight_beam(ifc_elem, axis, name, sec, mat, ifc_store)


def get_beam_geom(ifc_elem, ifc_settings):
    # from .read_shapes import get_ifc_geometry
    # pdct_shape, colour, alpha = get_ifc_geometry(ifc_elem, ifc_settings)

    bodies = [rep for rep in ifc_elem.Representation.Representations if rep.RepresentationIdentifier == "Body"]
    if len(bodies) != 1:
        raise ValueError("Number of body objects attached to element is not 1")
    if len(bodies[0].Items) != 1:
        raise ValueError("Number of items objects attached to body is not 1")

    body = bodies[0].Items[0]
    if len(body.StyledByItem) > 0:
        style = body.StyledByItem[0].Styles[0].Styles[0].Styles[0]
        colour = (int(style.SurfaceColour.Red), int(style.SurfaceColour.Green), int(style.SurfaceColour.Blue))
        print(colour)


def import_straight_beam(ifc_elem, axis, name, sec, mat, ifc_store: IfcStore) -> Beam:
    bodies = list(get_product_definitions(ifc_elem))
    if len(bodies) != 1:
        raise ValueError("Number of body objects attached to element is not 1")
    body = bodies[0]

    rel_place = Placement.from_axis3d(axis3d(ifc_elem.ObjectPlacement.RelativePlacement))

    extrude_dir = unit_vector(rel_place.transform_vector(body.position.axis, inverse=True))
    ref_dir = unit_vector(rel_place.transform_vector(body.position.ref_direction))

    origin = rel_place.origin
    local_y = calc_yvec(ref_dir, extrude_dir)
    p2 = origin + extrude_dir * body.depth

    return Beam(
        name,
        origin,
        p2,
        sec=sec,
        mat=mat,
        up=local_y,
        guid=ifc_elem.GlobalId,
        ifc_store=ifc_store,
        units=ifc_store.assembly.units,
    )


def import_revolved_beam(ifc_elem, axis, name, sec, mat, ifc_store: IfcStore) -> Beam:
    from ada import Placement
    from ada.core.vector_transforms import transform3d

    logger.warning("Reading IFC Beams swept along IfcTrimmedCurve is WIP")

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
    raise NotImplementedError("Reading beams swept along IfcPolyLines of length > 2 is not yet supported")
