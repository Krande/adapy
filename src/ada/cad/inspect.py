"""Backend-neutral shape introspection helpers built on ``active_backend()``.

These wrap the ``CadBackend`` verbs (``faces``/``face_plane``/``vertex_points``/
``shape_type``/``face_surface_type``) so callers and tests can introspect a
built shape without importing ``OCC.Core`` directly — they work under adacpp as
well as pythonocc. A raw (non-handle) pythonocc ``TopoDS_Shape`` is still
accepted via a lazily-imported OCC fallback, for construction-internal callers.
"""

from __future__ import annotations

from typing import Iterable, Iterator

from ada.geom.direction import Direction
from ada.geom.points import Point


def points_of(shape) -> list[tuple[float, float, float]]:
    """All vertex points of ``shape`` as ``(x, y, z)`` tuples."""
    from ada.cad import active_backend, is_shape_handle

    if is_shape_handle(shape):
        return active_backend().vertex_points(shape)

    # Raw pythonocc shape (construction-internal) — OCC fallback.
    from OCC.Core.BRep import BRep_Tool
    from OCC.Extend.TopologyUtils import TopologyExplorer

    points = []
    for v in TopologyExplorer(shape).vertices():
        apt = BRep_Tool.Pnt(v)
        points.append((apt.X(), apt.Y(), apt.Z()))
    return points


def faces_with_normal(shape, normal, point_in_plane: Iterable | Point = None) -> Iterator:
    """Yield faces of ``shape`` whose plane normal is parallel to ``normal``
    (and, if ``point_in_plane`` is given, lie in that plane)."""
    from ada.api.transforms import EquationOfPlane
    from ada.cad import active_backend, is_shape_handle
    from ada.core.vector_utils import is_parallel

    normal = Direction(*normal)
    eop = EquationOfPlane(point_in_plane, normal) if point_in_plane is not None else None

    if is_shape_handle(shape):
        backend = active_backend()
        faces = backend.faces(shape)

        def _face_normal(f):
            res = backend.face_plane(f)
            return res if res is not None else (None, None)

    else:
        from ada.occ.utils import get_face_normal
        from OCC.Extend.TopologyUtils import TopologyExplorer

        faces = TopologyExplorer(shape).faces()
        _face_normal = get_face_normal

    for face in faces:
        point, n = _face_normal(face)
        if n is None or not is_parallel(n, normal):
            continue
        if eop is None or eop.calc_distance_to_point(point) == 0.0:
            yield face
