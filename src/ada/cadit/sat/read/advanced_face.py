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
    nums = chunks[idx + 1 : idx + 7]
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
            xs.append(cp[0])
            ys.append(cp[1])
            zs.append(cp[2])
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
        a[3] < b[0] - tol
        or b[3] < a[0] - tol
        or a[4] < b[1] - tol
        or b[4] < a[1] - tol
        or a[5] < b[2] - tol
        or b[5] < a[2] - tol
    )


def get_face_bound(acis_record: AcisRecord) -> list[geo_su.FaceBound]:
    """Gets the outer edge loop from the SAT object data.

    A face's loops are a linked list — ``loop.chunks[6]`` points to the next loop, ``chunks[7]``
    to the loop's first coedge, and ``chunks[16]`` is the loop kind (``periphery`` = outer boundary,
    ``hole`` = inner). The outer boundary is usually first, but ACIS sometimes orders a *degenerate*
    hole loop ahead of it: a single zero-length, curve-less coedge marking a surface singularity
    (its two vertices are the same point). ``iter_loop_coedges`` correctly steps over that coedge, so
    reading only the face's first loop then yields an empty wire and the whole plate fails to build
    (``build_advanced_face: wire build failed``) — dropping a valid plate.

    Walk the chain and take the outer boundary: the ``periphery`` loop if one is marked, else the
    first loop that actually carries edges. Inner holes are not represented here (the downstream
    planar/advanced-face builders take a single bound), matching the prior single-loop behaviour.
    """
    loop_ptr = acis_record.chunks[7]
    seen: set[str] = set()
    first_nonempty: list | None = None

    while loop_ptr and loop_ptr != "$-1" and loop_ptr not in seen:
        seen.add(loop_ptr)
        loop_rec = acis_record.sat_store.get(loop_ptr)
        if loop_rec is None:
            break
        edges = list(iter_loop_coedges(loop_rec))
        if edges:
            if first_nonempty is None:
                first_nonempty = edges
            # Prefer the periphery (outer) loop over any non-degenerate hole loop.
            if len(loop_rec.chunks) > 16 and loop_rec.chunks[16] == "periphery":
                return [geo_su.FaceBound(bound=geo_cu.EdgeLoop(edges), orientation=True)]
        loop_ptr = loop_rec.chunks[6]

    return [geo_su.FaceBound(bound=geo_cu.EdgeLoop(first_nonempty or []), orientation=True)]


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
                "spline surface CP bbox disjoint from face bbox " "(exppc peel landed on neighbouring surface)"
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


def get_face_same_sense(face_record: AcisRecord) -> bool:
    """Does the face's normal agree with its surface's natural one?

    ACIS splits this across two records: the ``face`` carries a
    forward/reversed sense, and a ``spline-surface`` carries one of its own,
    so the face normal is the composition of the two. IFC's ``SameSense`` is
    that composition, which is what the OCC builder and the IFC/STEP writers
    already consume.

    This used to be hardcoded True, which is a claim rather than a reading: a
    Genie export writes ``reversed`` on 1248 of 4532 spline surfaces and on 112
    plane faces, and every one of those came back with its normal flipped.
    """
    face_sense = face_record.chunks[11] if len(face_record.chunks) > 11 else "forward"
    surface_record = face_record.sat_store.get(face_record.chunks[10])
    # A plane-surface has no sense of its own (its normal is a vector it
    # states outright); only a spline-surface carries one.
    surface_sense = "forward"
    if surface_record.type == "spline-surface" and len(surface_record.chunks) > 6:
        if surface_record.chunks[6] in ("forward", "reversed"):
            surface_sense = surface_record.chunks[6]
    return (face_sense == "forward") == (surface_sense == "forward")


def create_advanced_face_from_sat(face_record: AcisRecord) -> geo_su.AdvancedFace:
    """Creates an AdvancedFace from the SAT object data."""
    same_sense = get_face_same_sense(face_record)
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
