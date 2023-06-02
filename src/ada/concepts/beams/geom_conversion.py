from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np


from ada.core.vector_utils import transform_csys_to_csys
from ada.geom import Geometry
from ada.geom.curves import Line
from ada.geom.placement import Direction, Axis2Placement3D
from ada.geom.points import Point
from ada.geom.solids import ExtrudedAreaSolid
from ada.geom.surfaces import (
    ArbitraryProfileDefWithVoids,
    ProfileType,
    FaceBasedSurfaceModel,
    FaceBound,
    ConnectedFaceSet,
    PolyLoop,
)

if TYPE_CHECKING:
    from ada.concepts.beams import Beam, BeamSweep, BeamRevolve


def straight_beam_to_geom(beam: Beam, is_solid=True) -> Geometry:
    if beam.section.type == beam.section.TYPES.IPROFILE:
        if is_solid:
            return ipe_to_geom(beam)
        else:
            return ipe_to_face_geom(beam)
    else:
        raise NotImplementedError(f"Beam section type {beam.section.type} not implemented")


def swept_beam_to_geom(beam: BeamSweep, is_solid=True) -> Geometry:
    if is_solid:
        return swept_beam_to_solid_geom(beam)
    else:
        return swept_beam_to_face_geom(beam)


def revolved_beam_to_geom(beam: BeamRevolve, is_solid=True) -> Geometry:
    if is_solid:
        return revolved_beam_to_solid_geom(beam)
    else:
        return revolved_beam_to_face_geom(beam)


def ipe_to_face_geom(beam: Beam) -> Geometry:
    sec_profile = beam.section.get_section_profile(is_solid=False)
    connected_faces = []
    extrude_dir = Direction(0, 0, 1)
    x_dir = Direction(1, 0, 0)

    rotation_matrix = transform_csys_to_csys(extrude_dir, x_dir, beam.xvec, beam.yvec)

    xv_l = extrude_dir * beam.length
    for c in sec_profile.outer_curve_disconnected:
        edge = c.get_edges_geom()
        if not isinstance(edge, Line):
            raise NotImplementedError("Only lines are supported for now")

        p1 = edge.start
        p2 = edge.end
        p3 = p2 + xv_l
        p4 = p1 + xv_l
        points = np.concatenate([p1, p2, p3, p4]).reshape(-1, 3)
        new_points = np.matmul(rotation_matrix, points.T).T + beam.n1.p
        poly_loop = PolyLoop(polygon=[Point(*p) for p in new_points])
        connected_faces += [ConnectedFaceSet([FaceBound(bound=poly_loop, orientation=True)])]

    geom = FaceBasedSurfaceModel(connected_faces)
    return Geometry(beam.guid, geom, beam.color)


def ipe_to_geom(beam: Beam) -> Geometry:
    sec_profile = beam.section.get_section_profile()

    outer_curve = sec_profile.outer_curve.get_edges_geom()
    inner_curves = []
    if sec_profile.inner_curve is not None:
        inner_curves += [sec_profile.inner_curve.get_edges_geom()]

    profile = ArbitraryProfileDefWithVoids(ProfileType.AREA, outer_curve, inner_curves)
    place = Axis2Placement3D(location=beam.n1.p, axis=beam.xvec, ref_direction=beam.yvec)
    geom = ExtrudedAreaSolid(profile, place, beam.length, Direction(0, 0, 1))
    return Geometry(beam.guid, geom, beam.color)
