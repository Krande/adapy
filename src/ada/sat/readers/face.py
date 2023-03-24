from __future__ import annotations

from typing import TYPE_CHECKING

from ada.sat.exceptions import InsufficientPointsError

if TYPE_CHECKING:
    from ada.sat.factory import SatStore


class PlateFactory:
    # Face row
    name_idx = 2
    loop_idx = 7

    # Loop row
    coedge_ref = 7

    def __init__(self, sat_store: SatStore):
        self.sat_store = sat_store

    def get_face_name_and_points(self, face_data_str: str) -> tuple[str, list[tuple[float]]]:
        res = face_data_str.strip().split()

        name = self.sat_store.get_name(res[self.name_idx])
        if not name.startswith("FACE"):
            raise NotImplementedError(f"Only face_refs starting with 'FACE' is supported. Found {name}")

        edges = self.get_edges(res)
        points = self.get_points(edges)
        return name, points

    def get_points(self, edges: list[list[str]]) -> list[tuple[float]]:
        p1, p2 = self.get_points_from_edge(edges[0])

        points = [p1, p2]

        for coedge in edges:
            p1, p2 = self.get_points_from_edge(coedge)
            edge_direction = str(coedge[-4])
            if edge_direction == "forward":
                p = p2
            else:
                p = p1
            if p not in points:
                points.append(p)

        if len(points) < 3:
            raise InsufficientPointsError("Plates cannot have < 3 points")

        coedge_first_direction = str(edges[0][-4])
        if coedge_first_direction == "reversed":
            points.reverse()

        return points

    def get_edges(self, face_data_list: list[str]) -> list[list[str]]:
        loop = self.sat_store.get(face_data_list[self.loop_idx])
        coedge_start_id = loop[self.coedge_ref]
        coedge_first = self.sat_store.get(coedge_start_id)

        coedge_first_direction = str(coedge_first[-4])

        # Coedge row
        next_coedge_idx = 6 if coedge_first_direction == "forward" else 7

        next_coedge = True
        coedge_next_id = coedge_first[next_coedge_idx]
        edges = [coedge_first]

        max_iter = 500
        i = 0
        while next_coedge is True:
            coedge = self.sat_store.get(coedge_next_id)
            edges.append(coedge)

            coedge_next_id = coedge[next_coedge_idx]
            if coedge_next_id == coedge_start_id:
                next_coedge = False

            i += 1
            if i > max_iter:
                raise ValueError(f"Found {i} points which is over max={max_iter}")
        return edges

    def get_points_from_edge(self, coedge: list[str]):
        # Coedge row
        edge_ref = 9

        # Edge row
        vert1_idx = 6
        vert2_idx = 8
        # edge_type_idx = 8

        # Vertex row
        p_idx = -2

        edge = self.sat_store.get(coedge[edge_ref])
        vert1 = self.sat_store.get(edge[vert1_idx])
        vert2 = self.sat_store.get(edge[vert2_idx])
        # edge_type = get_value_from_satd(edge[edge_type_idx], satd)
        p1 = self.sat_store.get(vert1[p_idx])
        p2 = self.sat_store.get(vert2[p_idx])
        n1 = tuple([float(x) for x in p1[-4:-1]])
        n2 = tuple([float(x) for x in p2[-4:-1]])
        return n1, n2
