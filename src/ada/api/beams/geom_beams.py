from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

import ada.geom.curves
import ada.geom.solids as geo_so
import ada.geom.surfaces as geo_su
from ada.config import Config
from ada.core.vector_transforms import transform_csys_to_csys
from ada.geom import Geometry
from ada.geom.booleans import BooleanOperation
from ada.geom.curves import Circle, Edge
from ada.geom.placement import Axis2Placement3D, Direction
from ada.geom.points import Point

if TYPE_CHECKING:
    from ada import PipeSegStraight, Section
    from ada.api.beams import Beam, BeamRevolve, BeamSweep, BeamTapered


def straight_beam_to_geom(beam: Beam | PipeSegStraight, is_solid=True) -> Geometry:
    vec = beam.xvec
    yvec = beam.yvec
    p1 = beam.n1.p
    if Config().ifc_export_include_ecc and beam.e1 is not None:
        e1 = beam.e1
        vec = beam.xvec_e
        p1 = tuple([float(x) + float(e1[i]) for i, x in enumerate(beam.n1.p.copy())])

    if is_solid:
        profile = section_to_arbitrary_profile_def_with_voids(beam.section)
        place = Axis2Placement3D(location=p1, axis=vec, ref_direction=yvec)
        solid = geo_so.ExtrudedAreaSolid(profile, place, beam.length, Direction(0, 0, 1))
        geom = Geometry(beam.guid, solid, beam.color)
    else:
        if beam.section.type in (
            beam.section.TYPES.IPROFILE,
            beam.section.TYPES.TPROFILE,
            beam.section.TYPES.ANGULAR,
            beam.section.TYPES.CHANNEL,
            beam.section.TYPES.FLATBAR,
        ):
            geom = profile_disconnected_to_face_geom(beam)
        elif beam.section.type == beam.section.TYPES.BOX:
            geom = box_to_face_geom(beam)
        elif beam.section.type in (beam.section.TYPES.TUBULAR, beam.section.TYPES.CIRCULAR):
            # Tubular shell is represented by the outer surface of the shell.
            geom = circ_to_face_geom(beam)
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
    elif beam.section.type == beam.section.TYPES.BOX:
        if is_solid:
            return boxbeam_taper_to_geom(beam)
        else:
            raise NotImplementedError("Box beam taper to face geometry not implemented")
    else:
        raise NotImplementedError(f"Beam section type {beam.section.type} not implemented")


def swept_beam_to_face_geom(beam):
    pass


def swept_beam_to_geom(beam: BeamSweep, is_solid=True) -> Geometry:
    if is_solid:
        return swept_beam_to_solid_geom(beam)
    else:
        return swept_beam_to_face_geom(beam)


def revolved_beam_to_face_geom(beam):
    pass


def revolved_beam_to_geom(beam: BeamRevolve, is_solid=True) -> Geometry:
    if is_solid:
        return revolved_beam_to_solid_geom(beam)
    else:
        return revolved_beam_to_face_geom(beam)


def swept_beam_to_solid_geom(beam: BeamSweep) -> Geometry:
    return Geometry()


def revolved_beam_to_solid_geom(beam: BeamRevolve) -> Geometry:
    return Geometry()


def section_to_arbitrary_profile_def_with_voids(section: Section, solid=True) -> geo_su.ArbitraryProfileDef:
    inner_curves = []
    if section.type == section.TYPES.TUBULAR:
        outer_curve = Circle(Axis2Placement3D(), section.r)
        inner_curves += [Circle(Axis2Placement3D(), section.r - section.wt)]
    elif section.type == section.TYPES.CIRCULAR:
        outer_curve = Circle(Axis2Placement3D(), section.r)
    else:
        sec_profile = section.get_section_profile(is_solid=solid)
        outer_curve = sec_profile.outer_curve.curve_geom()
        if sec_profile.inner_curve is not None:
            inner_curves += [sec_profile.inner_curve.curve_geom()]

    if solid:
        profile_type = geo_su.ProfileType.AREA
    else:
        profile_type = geo_su.ProfileType.CURVE

    return geo_su.ArbitraryProfileDef(profile_type, outer_curve, inner_curves, profile_name=section.name)


def boxbeam_taper_to_geom(beam: BeamTapered) -> Geometry:
    profile1 = section_to_arbitrary_profile_def_with_voids(beam.section)
    profile2 = section_to_arbitrary_profile_def_with_voids(beam.taper)

    place = Axis2Placement3D(location=beam.n1.p, axis=beam.xvec, ref_direction=beam.yvec)
    geom = geo_so.ExtrudedAreaSolidTapered(profile1, place, beam.length, Direction(0, 0, 1), profile2)
    return Geometry(beam.guid, geom, beam.color)


def ibeam_taper_to_geom(beam: BeamTapered) -> Geometry:
    profile1 = section_to_arbitrary_profile_def_with_voids(beam.section)
    profile2 = section_to_arbitrary_profile_def_with_voids(beam.taper)

    place = Axis2Placement3D(location=beam.n1.p, axis=beam.xvec, ref_direction=beam.yvec)
    geom = geo_so.ExtrudedAreaSolidTapered(profile1, place, beam.length, Direction(0, 0, 1), profile2)
    return Geometry(beam.guid, geom, beam.color)


def ibeam_taper_to_face_geom(beam: BeamTapered) -> Geometry:
    profile1 = section_to_arbitrary_profile_def_with_voids(beam.section, solid=False)
    profile2 = section_to_arbitrary_profile_def_with_voids(beam.taper, solid=False)

    place = Axis2Placement3D(location=beam.n1.p, axis=beam.xvec, ref_direction=beam.yvec)
    geom = geo_so.ExtrudedAreaSolidTapered(profile1, place, beam.length, Direction(0, 0, 1), profile2)

    return Geometry(beam.guid, geom, beam.color)


def profile_disconnected_to_face_geom(beam: Beam) -> Geometry:
    sec_profile = beam.section.get_section_profile(is_solid=False)
    connected_faces = []
    extrude_dir = Direction(0, 0, 1)
    x_dir = Direction(1, 0, 0)

    rotation_matrix = transform_csys_to_csys(extrude_dir, x_dir, beam.xvec, beam.yvec)

    xv_l = extrude_dir * beam.length
    for c in sec_profile.outer_curve_disconnected:
        edge = c.curve_geom()
        if not isinstance(edge, Edge):
            raise NotImplementedError("Only lines are supported for now")

        p1 = edge.start.get_3d()
        p2 = edge.end.get_3d()
        p3 = p2 + xv_l
        p4 = p1 + xv_l
        points = np.concatenate([p1, p2, p3, p4]).reshape(-1, 3)
        new_points = np.matmul(rotation_matrix, points.T).T + beam.n1.p
        poly_loop = ada.geom.curves.PolyLoop(polygon=[Point(*p) for p in new_points])
        connected_faces += [geo_su.ConnectedFaceSet([geo_su.FaceBound(bound=poly_loop, orientation=True)])]

    geom = geo_su.FaceBasedSurfaceModel(connected_faces)
    return Geometry(beam.guid, geom, beam.color)


def box_to_face_geom(beam: Beam) -> Geometry:
    sec_profile = beam.section.get_section_profile(is_solid=False)
    outer_curve = sec_profile.outer_curve.curve_geom()
    place = Axis2Placement3D(location=beam.n1.p, axis=beam.xvec, ref_direction=beam.yvec)
    profile = geo_su.ArbitraryProfileDef(geo_su.ProfileType.CURVE, outer_curve, [], profile_name=beam.section.name)
    solid = geo_so.ExtrudedAreaSolid(profile, place, beam.length, Direction(0, 0, 1))
    return Geometry(beam.guid, solid, beam.color)


def circ_to_face_geom(beam: Beam) -> Geometry:
    outer_curve = Circle(Axis2Placement3D(), beam.section.r)
    place = Axis2Placement3D(location=beam.n1.p, axis=beam.xvec, ref_direction=beam.yvec)
    profile = geo_su.ArbitraryProfileDef(geo_su.ProfileType.CURVE, outer_curve, [], profile_name=beam.section.name)
    solid = geo_so.ExtrudedAreaSolid(profile, place, beam.length, Direction(0, 0, 1))
    return Geometry(beam.guid, solid, beam.color)
