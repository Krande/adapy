from __future__ import annotations

import ada.geom.curves as geo_cu
import ada.geom.surfaces as geo_su
from ada import Direction, Point
from ada.cadit.sat.exceptions import ACISReferenceDataError
from ada.cadit.sat.read.bsplinesurface import create_bsplinesurface_from_sat
from ada.cadit.sat.read.curves import iter_loop_coedges
from ada.cadit.sat.read.sat_entities import AcisRecord


def _face_world_bbox(face_record: AcisRecord) -> tuple[float, float, float, float, float, float] | None:
    """Extract the SAT face record's declared 3D world bbox.

    ACIS face records carry a bbox flagged by ``T`` followed by 6 floats
    (xmin ymin zmin xmax ymax zmax). Returns ``None`` if the flag isn't
    present or the 6 floats can't be parsed — caller treats that as
    "no bbox" and skips the sanity check rather than failing.
    """
    chunks = face_record.chunks
    try:
        idx = chunks.index("T")
    except ValueError:
        return None
    nums = chunks[idx + 1: idx + 7]
    if len(nums) != 6:
        return None
    try:
        f = [float(x) for x in nums]
    except (TypeError, ValueError):
        return None
    return (f[0], f[1], f[2], f[3], f[4], f[5])


def _spline_surface_cp_bbox(surface) -> tuple[float, float, float, float, float, float] | None:
    cps = getattr(surface, "control_points_list", None)
    if not cps:
        return None
    xs, ys, zs = [], [], []
    for row in cps:
        for cp in row:
            xs.append(cp[0]); ys.append(cp[1]); zs.append(cp[2])
    if not xs:
        return None
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def _bboxes_disjoint(a, b, tol: float = 1e-3) -> bool:
    """Two AABBs are disjoint when they don't overlap on at least one axis.

    Uses a small tolerance to absorb floating-point edge cases where the
    surface and face bboxes touch exactly (legitimate — surface trimmed
    to the face's perimeter).
    """
    return (
        a[3] < b[0] - tol or b[3] < a[0] - tol
        or a[4] < b[1] - tol or b[4] < a[1] - tol
        or a[5] < b[2] - tol or b[5] < a[2] - tol
    )


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
        # Sanity-check: when an exppc-wrapped spline-surface is peeled to
        # its inner exactsur, the inner surface can be a *neighbouring*
        # patch (sharing one edge with the actual face) rather than the
        # surface this face uses. The 3D bbox of the inner exactsur's
        # control points then doesn't overlap the face's declared bbox
        # at all. Building an OCC face on this mismatched surface yields
        # garbage when the wire's pcurves UV-evaluate outside the
        # surface's parameter domain (extrapolation produces a 10-30 m
        # blown-up mesh). Reject the AdvancedFace at the source so the
        # upstream flat-plate fallback path handles these — much cleaner
        # than trying to detect the corruption downstream.
        face_bbox = _face_world_bbox(face_record)
        surf_bbox = _spline_surface_cp_bbox(face_surface)
        if face_bbox is not None and surf_bbox is not None and _bboxes_disjoint(surf_bbox, face_bbox):
            raise ACISReferenceDataError(
                "spline surface CP bbox disjoint from face bbox "
                "(exppc peel landed on neighbouring surface)"
            )
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
