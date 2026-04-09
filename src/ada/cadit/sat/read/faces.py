from __future__ import annotations

from typing import TYPE_CHECKING

from ada.cadit.sat.exceptions import ACISInsufficientPointsError
from ada.cadit.sat.read.sat_entities import AcisRecord
from ada.config import logger

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

        # Fix 1: choose the periphery loop for the face instead of blindly
        # using the first loop reference.
        loop = self._get_primary_loop(chunks)
        if loop is None:
            logger.warning(f"face: '{name}' has no usable loop. Skipping...")
            return None

        edges = self.get_edges_from_loop(loop)

        try:
            points = self.get_points(edges)
        except ACISInsufficientPointsError as e:
            logger.warning(f"face: '{name}' failed to get points due to {e}. Skipping...")
            return None

        # Fix 2: simplify polluted partial-split boundaries to the outer hull
        # when they look like a split rectangular/quadrilateral plate.
        points = self._simplify_split_plate_boundary(points)

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
        # Kept for compatibility; now uses Fix 1 loop selection.
        loop = self._get_primary_loop(face_data_list)
        if loop is None:
            raise ValueError("Face has no usable loop")
        return self.get_edges_from_loop(loop)

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

    def _project_points_to_2d(self, points: list[tuple[float, float, float]]):
        """
        Project planar 3D points to 2D by dropping the axis with the smallest span.
        Returns (pts2d, dropped_axis_index).
        """
        spans = []
        for axis in range(3):
            vals = [p[axis] for p in points]
            spans.append(max(vals) - min(vals))

        drop_axis = min(range(3), key=lambda i: spans[i])

        def to_2d(p):
            if drop_axis == 0:
                return (p[1], p[2])
            elif drop_axis == 1:
                return (p[0], p[2])
            else:
                return (p[0], p[1])

        return [to_2d(p) for p in points], drop_axis

    def _signed_area_2d(self, pts2d: list[tuple[float, float]]) -> float:
        area = 0.0
        n = len(pts2d)
        for i in range(n):
            x1, y1 = pts2d[i]
            x2, y2 = pts2d[(i + 1) % n]
            area += x1 * y2 - x2 * y1
        return 0.5 * area

    def _has_reflex_vertex(self, pts2d: list[tuple[float, float]], tol: float = 1e-9) -> bool:
        """
        Returns True if the polygon has at least one reflex (non-convex) vertex.
        """
        n = len(pts2d)
        if n < 4:
            return False

        area = self._signed_area_2d(pts2d)
        if abs(area) < tol:
            return False

        orientation = 1.0 if area > 0.0 else -1.0

        for i in range(n):
            ax, ay = pts2d[i - 1]
            bx, by = pts2d[i]
            cx, cy = pts2d[(i + 1) % n]

            cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
            if cross * orientation < -tol:
                return True

        return False

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

    def _polygon_scale_2d(self, pts2d: list[tuple[float, float]]) -> float:
        xs = [p[0] for p in pts2d]
        ys = [p[1] for p in pts2d]
        dx = max(xs) - min(xs)
        dy = max(ys) - min(ys)
        return max(dx, dy, 1.0)

    def _remove_near_collinear_points_2d(
        self,
        pts3d: list[tuple[float, float, float]],
        tol_factor: float = 1e-8,
    ) -> list[tuple[float, float, float]]:
        """
        Remove nearly-collinear vertices from a closed planar polygon while preserving order.
        """
        if len(pts3d) < 4:
            return pts3d

        pts2d, _ = self._project_points_to_2d(pts3d)
        scale = self._polygon_scale_2d(pts2d)
        tol = tol_factor * scale * scale

        cleaned = []
        n = len(pts3d)

        for i in range(n):
            p_prev_2d = pts2d[i - 1]
            p_curr_2d = pts2d[i]
            p_next_2d = pts2d[(i + 1) % n]

            # twice signed triangle area
            cross = (p_curr_2d[0] - p_prev_2d[0]) * (p_next_2d[1] - p_curr_2d[1]) - (p_curr_2d[1] - p_prev_2d[1]) * (
                p_next_2d[0] - p_curr_2d[0]
            )

            if abs(cross) > tol:
                cleaned.append(pts3d[i])

        return cleaned if len(cleaned) >= 3 else pts3d

    def _convex_hull_indices_2d(
        self,
        pts2d: list[tuple[float, float]],
        tol_digits: int = 9,
        cross_tol_factor: float = 1e-10,
    ) -> list[int]:
        """
        Monotonic chain convex hull.
        Returns indices into the original point list, in hull order.
        Uses tolerance so nearly-collinear noise does not keep extra vertices.
        """
        indexed = []
        seen = set()
        for i, p in enumerate(pts2d):
            key = (round(p[0], tol_digits), round(p[1], tol_digits))
            if key not in seen:
                indexed.append((p[0], p[1], i))
                seen.add(key)

        if len(indexed) < 3:
            return [x[2] for x in indexed]

        indexed.sort(key=lambda t: (t[0], t[1]))

        scale = self._polygon_scale_2d(pts2d)
        cross_tol = cross_tol_factor * scale * scale

        def cross(o, a, b):
            return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

        lower = []
        for p in indexed:
            while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= cross_tol:
                lower.pop()
            lower.append(p)

        upper = []
        for p in reversed(indexed):
            while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= cross_tol:
                upper.pop()
            upper.append(p)

        hull = lower[:-1] + upper[:-1]
        return [p[2] for p in hull]

    def _simplify_split_plate_boundary(
        self, points: list[tuple[float, float, float]]
    ) -> list[tuple[float, float, float]]:
        """
        Fix 2:
        For partial split plates where SAT boundary points include internal split
        edges, simplify to the outer convex hull and then remove nearly-collinear
        hull points. This keeps only the external contour.
        """
        if len(points) < 5:
            return points

        pts2d, _ = self._project_points_to_2d(points)

        # Only intervene when the extracted contour is non-convex / polluted.
        if not self._has_reflex_vertex(pts2d):
            return points

        hull_idx = self._convex_hull_indices_2d(pts2d)
        hull_points = [points[i] for i in hull_idx]

        if len(hull_points) < 3:
            return points

        if len(hull_points) >= len(points):
            return points

        hull_points = self._remove_near_collinear_points_2d(hull_points)

        if len(hull_points) < 3:
            return points

        hull2d, _ = self._project_points_to_2d(hull_points)

        # Preserve winding direction
        area_orig = self._signed_area_2d(pts2d)
        area_hull = self._signed_area_2d(hull2d)
        if area_orig * area_hull < 0.0:
            hull_points.reverse()

        return hull_points
