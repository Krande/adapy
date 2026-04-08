from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

import ada.geom.curves
import ada.geom.solids as geo_so
import ada.geom.surfaces as geo_su
from ada.core.vector_transforms import transform_csys_to_csys
from ada.geom import Geometry
from ada.geom.booleans import BooleanOperation
from ada.geom.curves import Circle, Edge
from ada.geom.direction import Direction
from ada.geom.placement import Axis2Placement3D
from ada.geom.points import Point

if TYPE_CHECKING:
    from ada import PipeSegStraight, Section
    from ada.api.beams import Beam, BeamSweep, BeamTapered


def straight_beam_to_geom(beam: Beam | PipeSegStraight, is_solid=True) -> Geometry:
    xvec = beam.xvec
    yvec = beam.yvec
    up = beam.up
    p1 = beam.n1.p
    p2 = beam.n2.p

    # ---- Apply placement rotation/translation to axes and endpoints (same as exporter intent) ----
    if beam.placement.is_identity() is False:
        ident_place = ada.Placement()
        place_abs = beam.placement.get_absolute_placement(include_rotations=True)

        if not np.allclose(place_abs.rot_matrix, ident_place.rot_matrix):
            ori_vectors = place_abs.transform_array_from_other_place(
                np.asarray([xvec, yvec, up]), ident_place, ignore_translation=True
            )
            xvec, yvec, up = (
                np.asarray(ori_vectors[0], float),
                np.asarray(ori_vectors[1], float),
                np.asarray(ori_vectors[2], float),
            )

            tra_vectors = place_abs.transform_array_from_other_place(np.asarray([p1, p2]), ident_place)
            p1, p2 = np.asarray(tra_vectors[0], float), np.asarray(tra_vectors[1], float)
        else:
            p1 = place_abs.origin + p1
            p2 = place_abs.origin + p2

    # ---- Apply Genie-equivalent curve_offset at BOTH ends ----
    data = beam.offset_helper.curve_offset_local()

    ox1, oy1, oz1 = map(float, data.get("end1", (0.0, 0.0, 0.0)))
    ox2, oy2, oz2 = map(float, data.get("end2", data.get("end1", (0.0, 0.0, 0.0))))  # fallback constant

    # ---- Section-specific visual corrections (apply to both ends consistently) ----
    if beam.section.type == beam.section.TYPES.ANGULAR:
        cgz = float(getattr(beam.section.properties, "Cgz", 0.0) or 0.0)
        oz1 = oz1 - cgz + float(beam.section.h)
        oz2 = oz2 - cgz + float(beam.section.h)

    if beam.section.type == beam.section.TYPES.TPROFILE:
        cgz = float(getattr(beam.section.properties, "Cgz", 0.0) or 0.0)
        oz1 = oz1 - cgz + float(beam.section.h) / 2.0
        oz2 = oz2 - cgz + float(beam.section.h) / 2.0

    # Offset endpoints in GLOBAL space using current axes
    p1_off = p1 + ox1 * xvec + oy1 * yvec + oz1 * up
    p2_off = p2 + ox2 * xvec + oy2 * yvec + oz2 * up

    # New axis & length derived from offset endpoints (this is what fixes Bm3/Bm4/Bm6 visuals)
    v = p2_off - p1_off
    L = float(np.linalg.norm(v))
    if L <= 1e-12:
        raise ValueError(f"Beam {getattr(beam, 'name', beam.guid)} has ~zero length after offsets")

    xvec2 = v / L

    # Rebuild y/up to stay orthonormal & close to original 'up'
    up0 = up / (np.linalg.norm(up) + 1e-30)
    ytmp = np.cross(up0, xvec2)
    yn = np.linalg.norm(ytmp)
    if yn <= 1e-12:
        # up parallel to x -> fall back to original yvec
        y0 = yvec / (np.linalg.norm(yvec) + 1e-30)
        ytmp = np.cross(y0, xvec2)
        yn = np.linalg.norm(ytmp)
        if yn <= 1e-12:
            # last resort: pick any perpendicular vector
            a = np.array([1.0, 0.0, 0.0])
            if abs(np.dot(a, xvec2)) > 0.9:
                a = np.array([0.0, 1.0, 0.0])
            ytmp = np.cross(a, xvec2)
            yn = np.linalg.norm(ytmp)

    yvec2 = ytmp / (yn + 1e-30)
    up2 = np.cross(xvec2, yvec2)
    up2 = up2 / (np.linalg.norm(up2) + 1e-30)

    if is_solid:
        profile = section_to_arbitrary_profile_def_with_voids(beam.section)
        # NOTE: in your convention Axis2Placement3D(axis=Z) is using xvec as "axis" (extrusion direction)
        place = Axis2Placement3D(location=p1_off, axis=xvec2, ref_direction=yvec2)
        solid = geo_so.ExtrudedAreaSolid(profile, place, L, Direction(0, 0, 1))
        geom = Geometry(beam.guid, solid, beam.color)
    else:
        # If you want shells/lines to match varying end offsets too,
        # you should update the downstream helpers similarly (use p1_off/p2_off and xvec2/yvec2/up2).
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
    elif beam.section.type == beam.section.TYPES.TPROFILE:
        if is_solid:
            return tbeam_taper_to_geom(beam)
        else:
            return ibeam_taper_to_face_geom(beam)
    elif beam.section.type in (beam.section.TYPES.BOX, beam.section.TYPES.POLY):
        if is_solid:
            return arbitrary_section_profile_taper_to_geom(beam)
        else:
            raise NotImplementedError("Arbitrary section profile beam taper to face geometry not implemented")
    else:
        raise NotImplementedError(f"Beam section type {beam.section.type} not implemented")


def swept_beam_to_face_geom(beam):
    pass


def swept_beam_to_geom(beam: BeamSweep, is_solid=True) -> Geometry:
    if is_solid:
        return swept_beam_to_solid_geom(beam)
    else:
        return swept_beam_to_face_geom(beam)


def swept_beam_to_solid_geom(beam: BeamSweep) -> Geometry:
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


def arbitrary_section_profile_taper_to_geom(beam: BeamTapered) -> Geometry:
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


def tbeam_taper_to_geom(beam: BeamTapered) -> Geometry:
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
