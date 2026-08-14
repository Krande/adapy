"""Loft helpers — build a swept solid from a sequence of closed point loops.

Thin Pythonic layer over OCC's ``BRepOffsetAPI_ThruSections``. Each
section is an :class:`ada.geom.curves.PolyLoop` describing a closed
polygon in 3D. The loft threads a ruled solid through them in order.

Public helpers cover the surrounding operations a typical loft workflow
needs: building a wire from a poly loop, intersecting the resulting
solid with a plane to extract a cross-section, and iterating the face
boundaries back out as poly loops for downstream plate construction.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Iterator, Sequence

import numpy as np

from ada.cad import active_backend
from ada.geom.curves import PolyLoop
from ada.geom.direction import Direction
from ada.geom.points import Point

# Loft is fully backend-neutral: every operation routes through the active CAD
# backend (loft_profiles / section_with_plane / transform / build / faces / wires
# / wire_points), so it works under adacpp as well as pythonocc with no OCC import
# anywhere in this module. The transform helpers compose a 4x4 affine matrix and
# hand it to ``CadBackend.transform``; ``planar_face_from_poly_loop`` builds a
# ``CurveBoundedPlane`` through ``CadBackend.build``. See the internal design notes Phase 1/2.
if TYPE_CHECKING:
    from ada.api.spatial.part import Part
    from ada.cad import CadBackend, ShapeHandle


def wire_from_poly_loop(loop: PolyLoop, backend: "CadBackend | None" = None) -> ShapeHandle:
    """Build a closed wire from a :class:`PolyLoop`.

    The loop is connected with straight edges. If the polygon's last
    point does not coincide with the first, a closing edge is appended.

    ``backend`` overrides the process-wide ``active_backend()`` — pass one shared
    backend instance so the whole loft stays on one kernel; a shape built by one
    backend cannot be read by another.
    """
    pts = list(loop.polygon)
    if len(pts) < 2:
        raise ValueError(f"PolyLoop needs at least 2 points, got {len(pts)}")

    coords = [(float(p.x), float(p.y), float(p.z)) for p in pts]
    # Close the loop if the last point doesn't coincide with the first.
    if not pts[0].is_equal(pts[-1]):
        coords.append(coords[0])
    return (backend or active_backend()).make_wire(coords)


def planar_face_from_poly_loop(loop: PolyLoop, backend: "CadBackend | None" = None) -> ShapeHandle:
    """Build a planar face bounded by ``loop``. Loop must be closed and planar.

    Routes through ``CadBackend.build`` of a :class:`CurveBoundedPlane` — the
    plane basis (origin/normal/x-dir) is derived from the loop points — so it
    works under adacpp and pythonocc alike with no kernel import here.

    ``backend`` overrides the process-wide ``active_backend()`` (see
    :func:`wire_from_poly_loop`).
    """
    from ada.api.curves import CurvePoly2d
    from ada.geom import Geometry
    from ada.geom.placement import Axis2Placement3D
    from ada.geom.surfaces import CurveBoundedPlane, Plane

    pts = [(float(p.x), float(p.y), float(p.z)) for p in loop.polygon]
    poly = CurvePoly2d.from_3d_points(pts)
    place = Axis2Placement3D(poly.origin, axis=poly.normal, ref_direction=poly.xdir)
    surface = CurveBoundedPlane(Plane(place), poly.curve_geom())
    return (backend or active_backend()).build(Geometry("planar_face", surface))


def loft_profiles(
    profiles: Sequence[PolyLoop],
    ruled: bool = True,
    is_solid: bool = True,
    backend: "CadBackend | None" = None,
) -> ShapeHandle:
    """Build a lofted solid (or shell) through the given section profiles.

    The profile polygons are connected in section order via the backend's
    ``loft_profiles`` (ThruSections under both OCC and adacpp). ``backend``
    overrides the process-wide ``active_backend()`` (see
    :func:`wire_from_poly_loop`).
    """
    if len(profiles) < 2:
        raise ValueError(f"loft_profiles needs at least 2 profiles, got {len(profiles)}")

    sections = [[(float(p.x), float(p.y), float(p.z)) for p in prof.polygon] for prof in profiles]
    return (backend or active_backend()).loft_profiles(sections, ruled, is_solid)


def intersect_with_plane(
    shape: ShapeHandle,
    plane_origin: Point,
    plane_normal: Direction = Direction(0.0, 0.0, 1.0),
    plane_size: float = 1000.0,
    backend: "CadBackend | None" = None,
) -> ShapeHandle:
    """Boolean-intersect ``shape`` with a finite planar face.

    ``plane_size`` is the half-extent of the cutting face — must
    comfortably exceed the lateral extent of ``shape`` so the
    intersection is the full cross-section, not a clipped band. ``backend``
    overrides the process-wide ``active_backend()`` (see
    :func:`wire_from_poly_loop`).
    """
    origin = (float(plane_origin.x), float(plane_origin.y), float(plane_origin.z))
    normal = (float(plane_normal[0]), float(plane_normal[1]), float(plane_normal[2]))
    return (backend or active_backend()).section_with_plane(shape, origin, normal, plane_size)


def iter_face_poly_loops(shape: ShapeHandle, backend: "CadBackend | None" = None) -> Iterator[PolyLoop]:
    """Yield the outer-wire vertex loop of every face in ``shape``.

    Vertex order follows the wire's natural orientation; callers that
    care about winding (eg. plate normal direction) should reverse the
    polygon themselves. ``backend`` overrides the process-wide
    ``active_backend()`` and MUST be the backend that built ``shape`` (see
    :func:`wire_from_poly_loop`).
    """
    be = backend or active_backend()
    for face in be.faces(shape):
        wires = be.wires(face)
        if not wires:
            continue
        # First wire is the outer boundary; any further wires are holes.
        polygon = [Point(*p) for p in be.wire_points(wires[0])]
        if polygon:
            yield PolyLoop(polygon=polygon)


def loft_to_poly_loops(
    profiles: Sequence[PolyLoop], ruled: bool = True, backend: "CadBackend | None" = None
) -> list[PolyLoop]:
    """Convenience: loft and flatten to a list of face :class:`PolyLoop`s."""
    be = backend or active_backend()
    shape = loft_profiles(profiles, ruled=ruled, is_solid=True, backend=be)
    return list(iter_face_poly_loops(shape, backend=be))


def _affine_about_point(linear: np.ndarray, fixed: np.ndarray) -> np.ndarray:
    """Compose a 4x4 affine whose 3x3 linear part is ``linear`` and that holds
    ``fixed`` invariant: ``p' = linear @ (p - fixed) + fixed``. The translation
    column becomes ``fixed - linear @ fixed``."""
    m = np.eye(4)
    m[:3, :3] = linear
    m[:3, 3] = fixed - linear @ fixed
    return m


def translate_shape(
    shape: ShapeHandle, offset: Point | tuple[float, float, float], backend: "CadBackend | None" = None
) -> ShapeHandle:
    """Return a new shape translated by ``offset`` (backend-neutral). ``backend``
    overrides the process-wide ``active_backend()`` (see :func:`wire_from_poly_loop`)."""
    vec = offset if isinstance(offset, Point) else Point(offset)
    m = np.eye(4)
    m[:3, 3] = (float(vec.x), float(vec.y), float(vec.z))
    return (backend or active_backend()).transform(shape, m, True)


def rotate_shape(
    shape: ShapeHandle,
    axis_origin: Point | tuple[float, float, float],
    axis_direction: Direction | tuple[float, float, float],
    angle_deg: float,
    backend: "CadBackend | None" = None,
) -> ShapeHandle:
    """Return a new shape rotated by ``angle_deg`` around the given axis
    (backend-neutral; Rodrigues rotation about the axis through ``axis_origin``).
    ``backend`` overrides ``active_backend()`` (see :func:`wire_from_poly_loop`)."""
    origin = axis_origin if isinstance(axis_origin, Point) else Point(axis_origin)
    direction = axis_direction if isinstance(axis_direction, Direction) else Direction(axis_direction)

    d = np.array([float(direction[0]), float(direction[1]), float(direction[2])])
    d = d / np.linalg.norm(d)
    theta = math.radians(angle_deg)
    k = np.array([[0.0, -d[2], d[1]], [d[2], 0.0, -d[0]], [-d[1], d[0], 0.0]])
    linear = np.eye(3) + math.sin(theta) * k + (1.0 - math.cos(theta)) * (k @ k)
    fixed = np.array([float(origin.x), float(origin.y), float(origin.z)])
    return (backend or active_backend()).transform(shape, _affine_about_point(linear, fixed), True)


def mirror_shape(
    shape: ShapeHandle,
    plane_origin: Point | tuple[float, float, float],
    plane_normal: Direction | tuple[float, float, float],
    backend: "CadBackend | None" = None,
) -> ShapeHandle:
    """Return a new shape mirrored across the plane defined by origin + normal
    (backend-neutral; Householder reflection about the plane through ``plane_origin``).
    ``backend`` overrides ``active_backend()`` (see :func:`wire_from_poly_loop`)."""
    origin = plane_origin if isinstance(plane_origin, Point) else Point(plane_origin)
    normal = plane_normal if isinstance(plane_normal, Direction) else Direction(plane_normal)

    n = np.array([float(normal[0]), float(normal[1]), float(normal[2])])
    n = n / np.linalg.norm(n)
    linear = np.eye(3) - 2.0 * np.outer(n, n)
    fixed = np.array([float(origin.x), float(origin.y), float(origin.z)])
    return (backend or active_backend()).transform(shape, _affine_about_point(linear, fixed), True)


def loft_to_part(
    profiles: Sequence[PolyLoop],
    name: str,
    thickness: float = 0.01,
    ruled: bool = True,
    reverse_winding: bool = True,
    backend: "CadBackend | None" = None,
) -> "Part":  # forward ref to keep ada.api.loft import-light
    """Loft the profiles and pack each resulting face into an ``ada.Part`` of plates.

    Each face's outer wire becomes one :class:`ada.Plate` constructed via
    ``Plate.from_3d_points``. ``reverse_winding`` mirrors the convention
    used by upstream callers that flip the vertex order so the plate
    normal points outward. ``backend`` overrides ``active_backend()`` (see
    :func:`wire_from_poly_loop`)."""
    from ada.api.plates.base_pl import Plate
    from ada.api.spatial.part import Part
    from ada.core.utils import Counter

    be = backend or active_backend()
    shape = loft_profiles(profiles, ruled=ruled, is_solid=True, backend=be)
    counter = Counter(prefix=f"{name}_face_pl")
    plates = []
    for loop in iter_face_poly_loops(shape, backend=be):
        pts = [(float(p.x), float(p.y), float(p.z)) for p in loop.polygon]
        if reverse_winding:
            pts.reverse()
        plates.append(Plate.from_3d_points(next(counter), pts, thickness))

    part = Part(name)
    part /= plates
    return part


__all__ = [
    "wire_from_poly_loop",
    "planar_face_from_poly_loop",
    "loft_profiles",
    "intersect_with_plane",
    "iter_face_poly_loops",
    "loft_to_poly_loops",
    "loft_to_part",
    "translate_shape",
    "rotate_shape",
    "mirror_shape",
]
