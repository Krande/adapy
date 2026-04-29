from __future__ import annotations

from typing import TYPE_CHECKING

from ada.cadit.sat.exceptions import ACISInsufficientPointsError
from ada.cadit.sat.read.sat_entities import AcisRecord
from ada.config import logger
from ada.core.vector_utils import remove_near_collinear_points

if TYPE_CHECKING:
    from ada.cadit.sat.store import SatStore


class PlateFactory:
    # Face row
    name_idx = 2
    loop_idx = 7

    # Loop row
    next_loop_idx = 6
    coedge_ref = 7

    def __init__(self, sat_store: SatStore):
        self.sat_store = sat_store

    def get_face_name_and_points(self, acis_record: AcisRecord) -> tuple[str, list[tuple[float]]] | None:
        chunks = acis_record.chunks

        name = self.sat_store.get_name(chunks[self.name_idx])
        if not name.startswith("FACE"):
            raise NotImplementedError(f"Only face_refs starting with 'FACE' is supported. Found {name}")

        # Walk to the face's periphery loop; SAT faces can carry multiple loops
        # (periphery + holes) chained via the next-loop pointer, and the first
        # one is not necessarily the outer boundary.
        loop = self._get_primary_loop(chunks)
        if loop is None:
            logger.warning(f"face: '{name}' has no usable loop. Skipping...")
            return None

        edges = self.get_edges_from_loop(loop)
        edges = self._drop_whisker_coedges(edges)

        try:
            points = self.get_points(edges)
        except ACISInsufficientPointsError as e:
            logger.warning(f"face: '{name}' failed to get points due to {e}. Skipping...")
            return None

        points = remove_near_collinear_points(points)

        return name, points

    def _loop_type(self, loop: AcisRecord) -> str | None:
        for token in loop.chunks:
            if token == "periphery":
                return "periphery"
            if token == "hole":
                return "hole"
        return None

    def _iter_face_loops(self, face_data_list: list[str]):
        loop_id = face_data_list[self.loop_idx]
        visited = set()
        max_iter = 100
        i = 0

        while loop_id not in ("$-1", None) and loop_id not in visited:
            visited.add(loop_id)
            loop = self.sat_store.get(loop_id)
            yield loop

            loop_id = loop.chunks[self.next_loop_idx]

            i += 1
            if i > max_iter:
                raise ValueError(f"Loop traversal exceeded max={max_iter}")

    def _get_primary_loop(self, face_data_list: list[str]) -> AcisRecord | None:
        loops = list(self._iter_face_loops(face_data_list))
        if not loops:
            return None

        periphery_loop = next((lp for lp in loops if self._loop_type(lp) == "periphery"), None)
        return periphery_loop if periphery_loop is not None else loops[0]

    def get_points(self, edges: list[AcisRecord]) -> list[tuple[float]]:
        if not edges:
            # Loops that consist entirely of whisker coedge pairs are
            # stripped to nothing by `_drop_whisker_coedges`. Treat that
            # as the existing "not enough points" condition so the
            # caller's skip-and-warn path runs instead of an IndexError.
            raise ACISInsufficientPointsError("Plates cannot have 0 edges")

        p1, p2 = self.get_points_from_edge(edges[0])

        points = [p1, p2]

        for coedge in edges:
            p1, p2 = self.get_points_from_edge(coedge)
            edge_direction = str(coedge.chunks[-4])
            if edge_direction == "forward":
                p = p2
            else:
                p = p1
            if p not in points:
                points.append(p)

        if len(points) < 3:
            raise ACISInsufficientPointsError("Plates cannot have < 3 points")

        coedge_first_direction = str(edges[0].chunks[-4])
        if coedge_first_direction == "reversed":
            points.reverse()

        return points

    def get_edges(self, face_data_list: list[str]) -> list[AcisRecord]:
        loop = self._get_primary_loop(face_data_list)
        if loop is None:
            raise ValueError("Face has no usable loop")
        return self.get_edges_from_loop(loop)

    def _drop_whisker_coedges(self, coedges: list[AcisRecord]) -> list[AcisRecord]:
        """Remove coedge pairs that reference the same underlying edge.

        Some SAT loops contain dangling zero-width "whisker" edges where the
        loop walks out along an edge and immediately comes back. Those
        coedges share the same physical edge record. Keeping them produces a
        self-touching polygon that collapses to a spurious diagonal when the
        downstream vertex dedupe in :meth:`get_points` runs.
        """
        edge_ref_idx = 9
        refs = [c.chunks[edge_ref_idx] for c in coedges]
        counts: dict[str, int] = {}
        for r in refs:
            counts[r] = counts.get(r, 0) + 1
        paired = {r for r, n in counts.items() if n >= 2}
        if not paired:
            return coedges
        return [c for c, r in zip(coedges, refs) if r not in paired]

    def get_edges_from_loop(self, loop: AcisRecord) -> list[AcisRecord]:
        coedge_start_id = loop.chunks[self.coedge_ref]
        coedge_first = self.sat_store.get(coedge_start_id)

        coedge_first_direction = str(coedge_first.chunks[-4])

        # Coedge row
        next_coedge_idx = 6 if coedge_first_direction == "forward" else 7

        next_coedge = True
        coedge_next_id = coedge_first.chunks[next_coedge_idx]
        edges = [coedge_first]

        max_iter = 500
        i = 0
        while next_coedge is True:
            coedge = self.sat_store.get(coedge_next_id)
            edges.append(coedge)

            coedge_next_id = coedge.chunks[next_coedge_idx]
            if coedge_next_id == coedge_start_id:
                next_coedge = False

            i += 1
            if i > max_iter:
                raise ValueError(f"Found {i} points which is over max={max_iter}")
        return edges

    def get_points_from_edge(self, coedge: AcisRecord):
        # Coedge row
        edge_ref = 9

        # Edge row
        vert1_idx = 6
        vert2_idx = 8
        # edge_type_idx = 8

        # Vertex row
        p_idx = -2

        edge = self.sat_store.get(coedge.chunks[edge_ref])
        vert1 = self.sat_store.get(edge.chunks[vert1_idx])
        vert2 = self.sat_store.get(edge.chunks[vert2_idx])
        # edge_type = get_value_from_satd(edge[edge_type_idx], satd)
        p1 = self.sat_store.get(vert1.chunks[p_idx])
        p2 = self.sat_store.get(vert2.chunks[p_idx])
        n1 = tuple([float(x) for x in p1.chunks[-4:-1]])
        n2 = tuple([float(x) for x in p2.chunks[-4:-1]])
        return n1, n2
