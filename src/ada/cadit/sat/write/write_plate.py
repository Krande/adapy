from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np

import ada
import ada.geom.direction
from ada.base.types import GeomRepr
from ada.cadit.sat.utils import make_ints_if_possible
from ada.cadit.sat.write import sat_entities as se

if TYPE_CHECKING:
    from ada.cadit.sat.write.writer import SatWriter


def outline_ccw_about(points3d, normal) -> list:
    """The plate outline ordered counter-clockwise about ``normal``.

    ACIS derives which side of a face is material from the loop winding relative
    to the surface normal, so the two must agree. ``CurvePoly2d`` does not
    guarantee that: it re-orders the outline during construction and can hand
    back a loop wound *against* ``poly.normal`` (e.g. the 10x10 plate in
    ``test_write_basic_plate_sat`` comes back clockwise). Comparing the loop's
    own Newell normal to the declared one and flipping when they disagree keeps
    the emitted face oriented the way Genie writes it.
    """
    pts = np.asarray(points3d, dtype=float)
    newell = np.zeros(3)
    for i in range(len(pts)):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        newell += np.cross(a, b)
    if float(np.dot(newell, np.asarray(normal, dtype=float))) < 0:
        pts = pts[::-1]
    return [tuple(p) for p in pts]


def plate_to_sat_entities(pl: ada.Plate, face_name: str, geo_repr: GeomRepr, sw: SatWriter) -> list[se.SATEntity]:
    """Convert one :class:`~ada.Plate` into the ACIS entities for its face.

    The body/lump/shell are owned by the :class:`SatWriter` and shared by every
    plate (see :func:`part_to_sat_writer`); this only builds the face and the
    topology below it — loop, coedges, edges, vertices, points, curves — plus
    the plane surface and the DNV name/cached-plane attributes Genie expects.
    """
    if geo_repr != GeomRepr.SHELL:
        raise ValueError(f"Unsupported geometry representation: {geo_repr}")

    id_gen = sw.id_generator

    # Global coordinates: poly.points3d is in the plate's own frame, so a plate
    # inside a placed Part would otherwise land at the wrong position.
    points3d, normal = pl.outline_global()
    bbox = make_ints_if_possible([*np.min(points3d, axis=0), *np.max(points3d, axis=0)])
    # A plane-surface just needs any point on the plane for its origin; the mean
    # of the outline is on it by construction, and for the rectangular plates
    # Genie's reference files cover it is exactly the centroid they record.
    centroid = ada.Point(*np.mean(points3d, axis=0))

    sat_entities: list[se.SATEntity] = []

    face_id = id_gen.next_id()
    name_id = id_gen.next_id()
    loop_id = id_gen.next_id()

    surface = se.PlaneSurface(id_gen.next_id(), centroid, normal, pl.poly.xdir)
    cached_plane_attrib = se.CachedPlaneAttribute(id_gen.next_id(), face_id, name_id, centroid, normal)
    string_attrib_name = se.StringAttribName(name_id, face_name, face_id, cached_plane_attrib)

    face = se.Face(face_id, loop_id, sw.shell, string_attrib_name, surface)
    loop = se.Loop(loop_id, id_gen.next_id(), bbox, face=face)

    outline = outline_ccw_about(points3d, normal)
    n_seg = len(outline)

    coedge_ids = [loop.coedge if i == 0 else id_gen.next_id() for i in range(n_seg)]

    point_map = {}
    for p in outline:
        if tuple(p) not in point_map:
            point_map[tuple(p)] = se.SatPoint(id_gen.next_id(), ada.Point(*p))

    points = list(point_map.values())
    vertex_map = {p.id: se.Vertex(id_gen.next_id(), None, p) for p in points}
    vertices = list(vertex_map.values())

    edges = []
    coedges = []
    straight_curves = []

    for i in range(n_seg):
        start, end = outline[i], outline[(i + 1) % n_seg]
        coedge_id = coedge_ids[i]
        next_coedge_id = coedge_ids[(i + 1) % n_seg]
        prev_coedge_id = coedge_ids[i - 1]

        edge_id = id_gen.next_id()
        p1 = point_map.get(tuple(start))
        if p1 is None:
            raise ValueError(f"Point {start} not found in point_map")
        v1 = vertex_map.get(p1.id)
        if v1.edge is None:
            v1.edge = edge_id
        p2 = point_map.get(tuple(end))
        v2 = vertex_map.get(p2.id)
        if v2.edge is None:
            v2.edge = edge_id

        direction = ada.Direction(*(np.asarray(end, dtype=float) - np.asarray(start, dtype=float)))
        straight_curve = se.StraightCurve(id_gen.next_id(), p1.point, direction)
        edge = se.Edge(
            edge_id,
            v1,
            v2,
            coedge_id,
            straight_curve,
            start_pt=p1.point,
            end_pt=p2.point,
        )
        coedges.append(se.CoEdge(coedge_id, next_coedge_id, prev_coedge_id, edge, loop, "forward"))
        edges.append(edge)
        straight_curves.append(straight_curve)

    sat_entities += (
        [face, loop, string_attrib_name, cached_plane_attrib, surface]
        + coedges
        + edges
        + vertices
        + points
        + straight_curves
    )

    # The builders above wire some references by id (they are minted before the
    # target object exists); swap those for the objects now that all are built.
    sat_entity_map = {entity.id: entity for entity in sat_entities}
    for entity in sat_entities:
        for key, value in entity.__dict__.items():
            if key == "id" or "_idx" in key:
                continue
            if isinstance(value, int) and value in sat_entity_map:
                setattr(entity, key, sat_entity_map.get(value))

    return sorted(sat_entities, key=lambda x: x.id)
