"""Build the cell graph from partitioned solids.

Generic, kernel-agnostic: cells/faces/edges are opaque handles queried through
``ada.cad``; adjacency is discovered by matching rounded centroids — coincident
cell centroids are de-duplicated (highest priority wins), faces sharing a
centroid are linked as a shared connection, and edges sharing a centroid record
their neighbouring faces. Inspired by topologic's cell-complex extraction.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

import ada
from ada.cad import active_backend
from ada.topology.graph import (
    GOLDEN_SIDE_ORDER,
    FaceConnectionInfo,
    GraphCell,
    GraphEdge,
    GraphFace,
    _round_key,
)


@dataclass
class GraphCellExtractor:
    cells_in: list[GraphCell]

    graph_cells: list[GraphCell] = field(default_factory=list, init=False)
    _cell_centroid_map: dict = field(default_factory=lambda: defaultdict(list), init=False)

    def __post_init__(self):
        be = active_backend()
        for cell in self.cells_in:
            key = _round_key(be.center_of_mass(cell.handle))
            self._cell_centroid_map[key].append(cell)

    def extract(self) -> list[GraphCell]:
        self.extract_cells()
        self.extract_faces()
        self.extract_edges()
        return self.graph_cells

    def extract_cells(self) -> None:
        for cells in self._cell_centroid_map.values():
            cell = cells[0] if len(cells) == 1 else self._resolve_coincident(cells)
            dupes = [c for c in self.graph_cells if c.metadata.name == cell.metadata.name]
            if dupes:
                cell.suffix = str(len(dupes))
            self.graph_cells.append(cell)

    def extract_faces(self) -> None:
        be = active_backend()
        face_map: dict[tuple, list[GraphFace]] = defaultdict(list)
        for cell in self.graph_cells:
            cell_centroid = np.asarray(be.center_of_mass(cell.handle), dtype=float)
            for i, fh in enumerate(be.faces(cell.handle)):
                plane = be.face_plane(fh)
                normal = plane[1] if plane is not None else ada.Direction(0, 0, 1)
                centroid = be.center_of_mass(fh)
                # Orient outward: the face normal points away from the cell centre
                # (the kernel's geometric plane normal carries no consistent side).
                # Abutting cells then get opposite normals on a shared face.
                nvec = np.asarray(normal, dtype=float)
                if float(np.dot(nvec, np.asarray(centroid, dtype=float) - cell_centroid)) < 0:
                    normal = ada.Direction(*(-nvec))
                gf = GraphFace(fh, i, normal=normal, point_inside=centroid, parent_cell=cell)
                cell.faces.append(gf)
                # Prefer true topological identity: in a non-manifold complex an
                # internal face is one shared sub-shape referenced by two cells, so
                # its face_id matches across both. Fall back to the face's vertex
                # set when the backend can't provide an id (geometric match).
                fid = be.face_id(fh)
                if fid is None:
                    fid = ("vtx",) + tuple(sorted(_round_key(p) for p in be.wire_points(fh)))
                face_map[fid].append(gf)

            self._assign_feature_ids(cell)

        for faces in face_map.values():
            if len(faces) == 2:
                # A face shared with a cantilevered deck is NOT marked internal —
                # the abutting space wall stays external (topologic parity: the
                # old extractor did the same `has_cantilever: continue`).
                if any(getattr(f.parent_cell.metadata, "is_cantilevered_deck", False) for f in faces):
                    continue
                conn = FaceConnectionInfo(*faces)
                for f in faces:
                    f.shared_face_connection = conn

    def _assign_feature_ids(self, cell: GraphCell) -> None:
        # Kernel-independent face identity + ancestry. The outward normal of an
        # axis-aligned box face names its source feature (the box side); split
        # coplanar fragments share that side. Sorting by (golden side rank, centroid)
        # reproduces topologic's prism face order, so ``stable_face_id`` is fixed per
        # physical side regardless of OCC enumeration. Non-axis faces (loft cells)
        # get side=None -> rank 6, ordered by centroid (deterministic fallback).
        for f in cell.faces:
            f.source_cell = cell
            f.source_feature_id = f.get_side()

        def sort_key(f: GraphFace):
            rank = GOLDEN_SIDE_ORDER.get(f.source_feature_id, len(GOLDEN_SIDE_ORDER))
            c = f.point_inside
            return (rank, round(float(c[0]), 4), round(float(c[1]), 4), round(float(c[2]), 4))

        for sid, f in enumerate(sorted(cell.faces, key=sort_key)):
            f.stable_face_id = sid

    def extract_edges(self) -> None:
        be = active_backend()
        edge_map: dict[tuple, list[GraphEdge]] = defaultdict(list)
        for cell in self.graph_cells:
            for face in cell.faces:
                for j, eh in enumerate(be.edges(face.handle)):
                    pts = np.asarray(be.vertex_points(eh), dtype=float)
                    key = _round_key(pts.mean(axis=0))
                    ge = GraphEdge(eh, j, cell, face)
                    ge.source_cell = cell
                    ge.source_feature_id = face.source_feature_id
                    face.edges.append(ge)
                    edge_map[key].append(ge)
                # Stable, kernel-independent edge index: sort the face's edges by
                # midpoint so edge ids are fixed regardless of OCC enumeration order.
                for sid, ge in enumerate(
                    sorted(face.edges, key=lambda e: _round_key(np.asarray(be.vertex_points(e.handle)).mean(axis=0)))
                ):
                    ge.stable_edge_id = sid

        for edges in edge_map.values():
            if len(edges) == 1:
                continue
            for ge in edges:
                ge.connected_graph_faces = [e.parent_face for e in edges if e is not ge]

    def _resolve_coincident(self, cells: list[GraphCell]) -> GraphCell:
        # Highest-priority cell wins when several share a centroid.
        priorities = [self._priority(c) for c in cells]
        return cells[int(np.argmax(priorities))]

    @staticmethod
    def _priority(cell: GraphCell) -> float:
        v = cell.metadata.get("PRIORITY", 0)
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0
