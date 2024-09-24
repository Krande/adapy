from __future__ import annotations

import ada.geom.curves as geo_cu
import ada.geom.surfaces as geo_su
from ada import Direction, Point
from ada.cadit.sat.read.bsplinesurface import create_bsplinesurface_from_sat
from ada.cadit.sat.read.curves import iter_loop_coedges
from ada.cadit.sat.read.sat_entities import AcisRecord


def get_face_bound(acis_record: AcisRecord) -> list[geo_su.FaceBound]:
    """Gets the edge loop from the SAT object data."""

    loop_rec = acis_record.sat_store.get(acis_record.chunks[7])
    edges = []

    for edge in iter_loop_coedges(loop_rec):
        edges.append(edge)

    return [geo_su.FaceBound(bound=geo_cu.EdgeLoop(edges), orientation=True)]


def get_face_surface(face_record: AcisRecord) -> geo_su.SURFACE_GEOM_TYPES | geo_su.Plane:
    face_surface_record = face_record.sat_store.get(face_record.chunks[10])
    if face_surface_record.type == "spline-surface":
        face_surface = create_bsplinesurface_from_sat(face_surface_record)
    elif face_surface_record.type == "plane-surface":
        pos = Point(*[float(x) for x in face_surface_record.chunks[6:9]])
        normal = Direction(*[float(x) for x in face_surface_record.chunks[9:12]])
        ref_dir = Direction(*[float(x) for x in face_surface_record.chunks[12:15]])
        face_surface = geo_su.Plane(position=geo_su.Axis2Placement3D(location=pos, axis=normal, ref_direction=ref_dir))
    else:
        raise NotImplementedError(f"Unsupported surface type: {face_surface_record.type}")

    if face_surface is None:
        raise NotImplementedError(f"Unabal to create surface from {face_surface_record}")

    return face_surface


def create_planar_face_from_sat(face_record: AcisRecord) -> geo_su.ClosedShell:
    """Creates a PlanarFace from the SAT object data."""
    bounds = get_face_bound(face_record)
    face_surface = get_face_surface(face_record)
    if len(bounds) < 1:
        raise NotImplementedError(f"No bounds found for {face_record}")

    if len(bounds) > 1:
        raise NotImplementedError(f"Multiple bounds found for {face_record}")

    return geo_su.ClosedShell([geo_su.FaceSurface(bounds, face_surface, same_sense=True)])


def create_advanced_face_from_sat(face_record: AcisRecord) -> geo_su.AdvancedFace:
    """Creates an AdvancedFace from the SAT object data."""
    same_sense = True
    bounds = get_face_bound(face_record)

    face_surface = get_face_surface(face_record)

    if len(bounds) < 1:
        raise NotImplementedError(f"No bounds found for {face_record}")

    if face_surface is None:
        raise NotImplementedError(f"No face surface found for {face_record}")

    return geo_su.AdvancedFace(
        bounds=bounds,
        face_surface=face_surface,
        same_sense=same_sense,
    )
