from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

from ada.core.vector_utils import transform_csys_to_csys
from ada.geom import Geometry, BooleanOperation
from ada.geom.curves import Line, Circle
from ada.geom.placement import Axis2Placement3D, Direction
from ada.geom.points import Point
import ada.geom.solids as geo_so
import ada.geom.surfaces as geo_su

if TYPE_CHECKING:
    from ada import Section
    from ada.concepts.beams import Beam, BeamRevolve, BeamSweep, BeamTapered


def straight_beam_to_geom(beam: Beam, is_solid=True) -> Geometry:
    if is_solid:
        profile = section_to_arbitrary_profile_def_with_voids(beam.section)
        place = Axis2Placement3D(location=beam.n1.p, axis=beam.xvec, ref_direction=beam.yvec)
        solid = geo_so.ExtrudedAreaSolid(profile, place, beam.length, Direction(0, 0, 1))
        geom = Geometry(beam.guid, solid, beam.color)
    else:
        if beam.section.type == beam.section.TYPES.IPROFILE:
            geom = ibeam_to_face_geom(beam)
        elif beam.section.type == beam.section.TYPES.BOX:
            geom = box_to_face_geom(beam)
        else:
            raise NotImplementedError(f"Beam section type {beam.section.type} not implemented")

    geom.bool_operations = [BooleanOperation(x.primitive.solid_geom(), x.bool_op) for x in beam.booleans]
    return geom


def straight_tapered_beam_to_geom(beam: BeamTapered, is_solid=True) -> Geometry:
    if beam.section.type == beam.section.TYPES.IPROFILE:
        if is_solid:
            return ibeam_taper_to_geom(beam)
        else:
            return ibeam_taper_to_face_geom(beam)
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


def swept_beam_to_solid_geom(beam: BeamSweep) -> Geometry:
    return Geometry()


def revolved_beam_to_solid_geom(beam: BeamRevolve) -> Geometry:
    return Geometry()


def section_to_arbitrary_profile_def_with_voids(section: Section) -> geo_su.ArbitraryProfileDefWithVoids:
    inner_curves = []
    if section.type == section.TYPES.TUBULAR:
        outer_curve = Circle(Axis2Placement3D(), section.r)
        inner_curves += [Circle(Axis2Placement3D(), section.r - section.wt)]
    elif section.type == section.TYPES.CIRCULAR:
        outer_curve = Circle(Axis2Placement3D(), section.r)
    else:
        sec_profile = section.get_section_profile()
        outer_curve = sec_profile.outer_curve.get_edges_geom()
        if sec_profile.inner_curve is not None:
            inner_curves += [sec_profile.inner_curve.get_edges_geom()]

    return geo_su.ArbitraryProfileDefWithVoids(geo_su.ProfileType.AREA, outer_curve, inner_curves)


def ibeam_taper_to_geom(beam: BeamTapered) -> Geometry:
    profile1 = section_to_arbitrary_profile_def_with_voids(beam.section)
    profile2 = section_to_arbitrary_profile_def_with_voids(beam.taper)

    place = Axis2Placement3D(location=beam.n1.p, axis=beam.xvec, ref_direction=beam.yvec)
    geom = geo_so.ExtrudedAreaSolidTapered(profile1, place, beam.length, Direction(0, 0, 1), profile2)
    return Geometry(beam.guid, geom, beam.color)


def ibeam_to_face_geom(beam: Beam) -> Geometry:
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
        poly_loop = geo_su.PolyLoop(polygon=[Point(*p) for p in new_points])
        connected_faces += [geo_su.ConnectedFaceSet([geo_su.FaceBound(bound=poly_loop, orientation=True)])]

    geom = geo_su.FaceBasedSurfaceModel(connected_faces)
    return Geometry(beam.guid, geom, beam.color)


def box_to_face_geom(beam: Beam) -> Geometry:
    sec_profile = beam.section.get_section_profile(is_solid=False)
    profile = geo_su.ArbitraryProfileDefWithVoids(geo_su.ProfileType.AREA, outer_curve, inner_curves)
    geo_so.ExtrudedAreaSolid(profile, place, beam.length, Direction(0, 0, 1))
    return Geometry()
