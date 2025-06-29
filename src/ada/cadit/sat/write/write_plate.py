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


def plate_to_sat_entities(
    pl: ada.Plate, face_name: str, geo_repr: GeomRepr, sw: SatWriter, use_dual_assembly=False
) -> list[se.SATEntity]:
    """Convert a Plate object to a SAT entities."""

    if geo_repr != GeomRepr.SHELL:
        raise ValueError(f"Unsupported geometry representation: {geo_repr}")

    pmin = np.min(pl.poly.points3d, axis=0)
    pmax = np.max(pl.poly.points3d, axis=0)
    sat_entities = []
    bbox = [*pmin, *pmax]
    bbox = make_ints_if_possible(bbox)
    # Initialize strings for each component
    # Create main entities using ID generator

    id_gen = sw.id_generator
    bodies = sw.get_entities_by_type(se.Body)

    if len(bodies) == 0:
        body_id = id_gen.next_id()
        lump_id = id_gen.next_id()
        body = se.Body(body_id, lump_id, bbox)
        sat_entities.append(body)
    else:
        body = bodies[0]
        lump_id = id_gen.next_id()
    # update body bbox
    updated_body_bbox = [x for x in body.bbox]
    for i, x in enumerate(bbox):
        body_value = body.bbox[i]
        local_value = bbox[i]
        if i > 2:
            if local_value > body_value:
                updated_body_bbox[i] = local_value
        else:
            if local_value < body_value:
                updated_body_bbox[i] = local_value

    body.bbox = updated_body_bbox

    shell_id = id_gen.next_id()
    face_id = id_gen.next_id()
    lump = se.Lump(lump_id, shell_id, body, bbox)
    sat_entities.append(lump)
    shell = se.Shell(shell_id, face_id, lump, bbox)

    name_id = id_gen.next_id()
    loop_id = id_gen.next_id()

    surface = se.PlaneSurface(id_gen.next_id(), pl.poly.get_centroid(), pl.poly.normal, pl.poly.xdir)
    cache_plane_id = id_gen.next_id()
    if use_dual_assembly:
        fused_face_id = id_gen.next_id()

        posattr2_id = id_gen.next_id()
        posattr1 = se.PositionAttribName(id_gen.next_id(), posattr2_id, fused_face_id, face_id, bbox, "ExactBoxHigh")

        posattr2 = se.PositionAttribName(posattr2_id, cache_plane_id, posattr1, face_id, bbox, "ExactBoxLow")
        cached_plane_attrib = se.CachedPlaneAttribute(
            cache_plane_id, face_id, posattr2.id, pl.poly.get_centroid(), pl.poly.normal
        )
        fused_face_att = se.FusedFaceAttribute(fused_face_id, name_id, posattr1, face_id)
        string_attrib_name = se.StringAttribName(name_id, face_name, face_id, fused_face_att)
        sat_entities += [posattr1, posattr2, fused_face_att]
    else:
        cached_plane_attrib = se.CachedPlaneAttribute(
            id_gen.next_id(), face_id, name_id, pl.poly.get_centroid(), pl.poly.normal
        )
        string_attrib_name = se.StringAttribName(name_id, face_name, face_id, cached_plane_attrib)

    face = se.Face(face_id, loop_id, shell, string_attrib_name, surface)
    if use_dual_assembly:
        loop = se.Loop(loop_id, id_gen.next_id(), bbox, surface)
    else:
        loop = se.Loop(loop_id, id_gen.next_id(), bbox)

    edges = []
    coedges = []
    straight_curves = []

    seg3d = pl.poly.segments3d

    coedge_ids = []
    for i, edge in enumerate(seg3d):
        if i == 0:
            coedge_id = loop.coedge
        else:
            coedge_id = id_gen.next_id()
        coedge_ids.append(coedge_id)

    point_map = {}

    for segment in seg3d:
        if tuple(segment.p1) not in point_map.keys():
            point_map[tuple(segment.p1)] = se.SatPoint(id_gen.next_id(), segment.p1)
        if tuple(segment.p2) not in point_map.keys():
            point_map[tuple(segment.p2)] = se.SatPoint(id_gen.next_id(), segment.p2)

    points = list(point_map.values())
    vertex_map = {p.id: se.Vertex(id_gen.next_id(), None, p) for p in points}
    vertices = list(vertex_map.values())
    edge_seq = [(1, 2), (2, 3), (3, 4), (4, 1)]

    for i, edge in enumerate(seg3d):
        coedge_id = coedge_ids[i]
        if i == 0:
            next_coedge_id = coedge_ids[(i + 1)]
            prev_coedge_id = coedge_ids[-1]
        elif i == len(seg3d) - 1:
            next_coedge_id = coedge_ids[0]
            prev_coedge_id = coedge_ids[(i - 1)]
        else:
            next_coedge_id = coedge_ids[(i + 1)]
            prev_coedge_id = coedge_ids[(i - 1)]

        # start
        edge_id = id_gen.next_id()
        p1 = point_map.get(tuple(edge.p1))
        if p1 is None:
            raise ValueError(f"Point {edge.p1} not found in point_map")
        v1 = vertex_map.get(p1.id)
        if v1.edge is None:
            v1.edge = edge_id
        p2 = point_map.get(tuple(edge.p2))
        v2 = vertex_map.get(p2.id)
        if v2.edge is None:
            v2.edge = edge_id
        straight_curve = se.StraightCurve(id_gen.next_id(), p1.point, edge.direction)
        edge = se.Edge(
            edge_id,
            v1,
            v2,
            coedge_id,
            straight_curve,
            start_pt=p1.point,
            end_pt=p2.point,
        )
        if use_dual_assembly:
            edge_n = f"EDGE{sw.edge_name_id:08d}"
            edge_str_id = id_gen.next_id()
            length = ada.Direction(p1.point - p2.point).get_length()
            fusedge = se.FusedEdgeAttribute(
                id_gen.next_id(),
                name=edge_str_id,
                entity=edge,
                edge_idx=i + 1,
                edge_seq=edge_seq[i],
                edge_length=length,
            )
            edge_string_att = se.StringAttribName(edge_str_id, edge_n, edge, attrib_ref=fusedge)
            edge.attrib_name = edge_string_att
            sat_entities.append(edge_string_att)
            sat_entities.append(fusedge)
            sw.edge_name_id += 1

        coedge = se.CoEdge(coedge_id, prev_coedge_id, next_coedge_id, edge, loop, "forward")
        coedges.append(coedge)
        edges.append(edge)
        straight_curves.append(straight_curve)

    sat_entities += (
        [
            shell,
            face,
            loop,
            string_attrib_name,
            cached_plane_attrib,
            surface,
        ]
        + coedges
        + edges
        + vertices
        + points
        + straight_curves
    )

    sat_entity_map = {entity.id: entity for entity in sat_entities}
    for entity in sat_entities:
        for key, value in entity.__dict__.items():
            if key == "id" or "_idx" in key:
                continue
            if isinstance(value, int) and value in sat_entity_map.keys():
                setattr(entity, key, sat_entity_map.get(value))

    sorted_entities = sorted(sat_entities, key=lambda x: x.id)

    return sorted_entities
