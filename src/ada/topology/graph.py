"""Cell-graph data model: cells, their faces/edges, and adjacency.

Generic, domain-free core. Geometry is queried through ``ada.cad`` verbs on
opaque ``ShapeHandle``s (a cell is a solid handle, a face a face handle, an edge
an edge handle); identity/adjacency is keyed on rounded centroids. Metadata is a
plain :class:`~ada.topology.metadata.TopologyMetadata` side-table.

Domain layers extend this by subclassing ``GraphFace``/``GraphCell`` (to add
typed accessors) and overriding the small neutral hooks — ``GraphFace.should_skip``
(default ``False``) and ``CellGraph._priority`` (default reads
``metadata['PRIORITY']`` or 0). Inspired by topologic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import groupby
from typing import TYPE_CHECKING, Iterable

import numpy as np

import ada
from ada.api.transforms import EquationOfPlane
from ada.cad import ShapeHandle, active_backend
from ada.core.guid import create_guid
from ada.core.vector_utils import get_centroid
from ada.topology.grid import CellGrid  # noqa: F401  (re-exported convenience)
from ada.topology.metadata import TopologyMetadata

if TYPE_CHECKING:
    pass

CUBE_INDEX_NORMAL_MAP = {
    0: (0, 0, -1),
    1: (0, 0, 1),
    2: (0, -1, 0),
    3: (0, 1, 0),
    4: (-1, 0, 0),
    5: (1, 0, 0),
}
SIDE_TO_INDEX = {"-Z": 0, "Z": 1, "-Y": 2, "Y": 3, "-X": 4, "X": 5}
INDEX_TO_SIDE = {v: k for k, v in SIDE_TO_INDEX.items()}


def _round_key(pt, ndigits: int = 4) -> tuple[float, float, float]:
    return (round(float(pt[0]), ndigits), round(float(pt[1]), ndigits), round(float(pt[2]), ndigits))


@dataclass
class GraphEdge:
    handle: ShapeHandle = field(repr=False)
    index: int
    parent_cell: "GraphCell" = field(repr=False)
    parent_face: "GraphFace" = field(repr=False)
    connected_graph_faces: list["GraphFace"] = field(default_factory=list, repr=False)

    _points: list[ada.Point] | None = None
    _is_hor: bool | None = None

    def __hash__(self):
        return id(self)

    def get_connected_face_by_normal(self, face_normal: ada.Direction, tolerant_direction=True) -> list["GraphFace"]:
        out = []
        for face in self.connected_graph_faces:
            if face.normal.is_equal(face_normal) or (tolerant_direction and face.normal.is_equal(-face_normal)):
                out.append(face)
        return out

    def is_external(self) -> bool:
        return len(self.get_connected_face_by_normal(self.parent_face.normal)) == 0

    def get_points(self) -> list[ada.Point]:
        if self._points is None:
            self._points = [ada.Point(*p) for p in active_backend().vertex_points(self.handle)]
        return self._points

    def get_direction(self) -> ada.Direction:
        pts = self.get_points()
        return ada.Direction(pts[1] - pts[0])

    def is_horizontal(self) -> bool:
        if self._is_hor is None:
            self._is_hor = abs(self.get_direction().get_normalized())[2] == 0
        return self._is_hor

    def get_name(self):
        return f"{self.parent_face.get_name()}_Edge_{self.index}"

    @property
    def shared_connections(self) -> int:
        return len(self.connected_graph_faces)

    def __repr__(self):
        return f"GraphEdge(index={self.index}, shared_connections={self.shared_connections})"


@dataclass
class FaceConnectionInfo:
    # Two cells connect through exactly one shared face.
    face1: "GraphFace"
    face2: "GraphFace"


@dataclass
class GraphFace:
    handle: ShapeHandle = field(repr=False)
    index: int
    normal: ada.Direction
    point_inside: ada.Point
    parent_cell: "GraphCell"
    edges: list[GraphEdge] = field(default_factory=list)
    shared_face_connection: FaceConnectionInfo | None = None
    associated_part: "ada.Part | None" = None
    openings: list = field(default_factory=list)
    guid: str = field(default_factory=create_guid, init=False)
    build_props: dict = field(default_factory=dict)

    _name: str | None = None
    _points: list[ada.Point] | None = None
    _centroid: ada.Point | None = None
    _is_hor: bool | None = None
    _normal_position: float | None = None
    _name_gen: "ada.Counter | None" = None

    def __post_init__(self):
        self._name_gen = ada.Counter(prefix=self.name)

    def __hash__(self):
        return hash(self.guid)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GraphFace):
            return NotImplemented
        return self.guid == other.guid

    @property
    def is_external(self) -> bool:
        return self.shared_face_connection is None

    def is_side(self, side: str) -> bool:
        if side not in SIDE_TO_INDEX:
            raise ValueError(f"Invalid side '{side}'. Must be one of {list(SIDE_TO_INDEX)}")
        return self.normal.is_equal(ada.Direction(*CUBE_INDEX_NORMAL_MAP[SIDE_TO_INDEX[side]]))

    def get_side(self) -> str | None:
        for idx, vec in CUBE_INDEX_NORMAL_MAP.items():
            if self.normal.is_equal(ada.Direction(*vec)):
                return INDEX_TO_SIDE[idx]
        return None

    def get_normal_position(self) -> float:
        if self._normal_position is None:
            self._normal_position = float(np.abs(self.normal).dot(self.point_inside))
        return self._normal_position

    def get_points(self) -> list[ada.Point]:
        if self._points is None:
            self._points = [ada.Point(*p) for p in active_backend().wire_points(self.handle)]
        return self._points

    def get_centroid(self) -> ada.Point:
        if self._centroid is None:
            self._centroid = get_centroid(self.get_points())
        return self._centroid

    def get_space_name(self) -> str:
        return self.parent_cell.name

    def get_equation_of_plane(self) -> EquationOfPlane:
        return EquationOfPlane(self.point_inside, self.normal)

    def get_connecting_coplanar_faces(self) -> list["GraphFace"]:
        neighbours = set()
        for edge in self.edges:
            if not edge.connected_graph_faces:
                continue
            for other in edge.get_connected_face_by_normal(self.normal):
                if self.shared_face_connection and other in (
                    self.shared_face_connection.face1,
                    self.shared_face_connection.face2,
                ):
                    continue
                if other:
                    neighbours.add(other)
        return list(neighbours)

    def get_connecting_faces(self) -> list["GraphFace"]:
        neighbours = set()
        for edge in self.edges:
            for other in edge.connected_graph_faces:
                if other != self:
                    neighbours.add(other)
        return list(neighbours)

    def get_adjacent_cell(self) -> "GraphCell | None":
        if self.shared_face_connection is None:
            return None
        if self.shared_face_connection.face1 == self:
            return self.shared_face_connection.face2.parent_cell
        return self.shared_face_connection.face1.parent_cell

    def is_horizontal(self) -> bool:
        if self._is_hor is None:
            self._is_hor = self.normal.is_equal(ada.Direction(0, 0, 1)) or self.normal.is_equal(ada.Direction(0, 0, -1))
        return self._is_hor

    def is_wall(self) -> bool:
        return not self.is_horizontal()

    def is_floor(self) -> bool:
        if not self.is_horizontal():
            return False
        return self.get_normal_position() < self.parent_cell.get_centroid().z

    def is_roof(self) -> bool:
        if not self.is_horizontal():
            return False
        return self.get_normal_position() > self.parent_cell.get_centroid().z

    @property
    def name(self) -> str:
        if self._name is None:
            self._name = f"{self.parent_cell.name}_f{self.index}"
        return self._name

    def get_name(self) -> str:
        return self.name

    def get_new_name(self) -> str:
        return next(self._name_gen)

    def should_skip(self) -> bool:
        # Neutral hook: domain subclasses override to exclude faces (e.g. by
        # metadata-driven include/exclude indices).
        return False

    def __repr__(self):
        return (
            f"{self.__class__.__name__}(name={self.name}, index={self.index}, "
            f"normal={self.normal}, is_external={self.is_external})"
        )


@dataclass
class GraphCell:
    handle: ShapeHandle = field(repr=False)
    faces: list[GraphFace]
    metadata: TopologyMetadata
    cell_graph: "CellGraph | None" = None
    suffix: str | None = None
    build_props: dict = field(default_factory=dict)

    _centroid: ada.Point | None = None
    _cog: ada.Point | None = None

    @property
    def name(self) -> str:
        if self.suffix is None:
            return self.metadata.name
        return f"{self.metadata.name}_{self.suffix}"

    def get_faces_from_index(self, idx: int) -> list[GraphFace]:
        face_normal = ada.Direction(*CUBE_INDEX_NORMAL_MAP[idx])
        return [f for f in self.faces if f.normal.is_equal(face_normal)]

    def get_faces_from_side(self, side: str) -> list[GraphFace]:
        if side not in SIDE_TO_INDEX:
            raise ValueError(f"Invalid side '{side}'. Must be one of {list(SIDE_TO_INDEX)}")
        return self.get_faces_from_index(SIDE_TO_INDEX[side])

    def get_centroid(self) -> ada.Point:
        # Vertex centroid (average of the cell's corner points).
        if self._centroid is None:
            pts = active_backend().vertex_points(self.handle)
            self._centroid = ada.Point(*(np.mean(np.asarray(pts, dtype=float), axis=0)))
        return self._centroid

    def get_cog(self) -> ada.Point:
        # Centre of mass (kernel BRepGProp).
        if self._cog is None:
            self._cog = active_backend().center_of_mass(self.handle)
        return self._cog

    def get_points(self) -> list[ada.Point]:
        return [ada.Point(*p) for p in active_backend().vertex_points(self.handle)]

    def __repr__(self):
        return f"{self.__class__.__name__}(name={self.name}, faces={len(self.faces)})"


@dataclass
class CellGraph:
    cells: list[GraphCell]

    grid_faces: CellGrid = field(default_factory=CellGrid)
    grid_pri_girders: CellGrid = field(default_factory=CellGrid)
    grid_sec_girders: CellGrid = field(default_factory=CellGrid)
    grid_stiffeners: CellGrid = field(default_factory=CellGrid)

    def __post_init__(self):
        for cell in self.cells:
            if cell.cell_graph is None:
                cell.cell_graph = self

    # --- neutral hooks (domain subclasses override) -----------------------
    def _priority(self, cell: GraphCell) -> float:
        v = cell.metadata.get("PRIORITY", 0)
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0

    # --- iteration --------------------------------------------------------
    def _iter_horizontal_sides(self) -> Iterable[GraphFace]:
        for cell in self.cells:
            for side in cell.faces:
                if side.should_skip():
                    continue
                if side.is_horizontal():
                    yield side

    def _iter_non_horizontal_sides(self) -> Iterable[GraphFace]:
        for cell in self.cells:
            for side in cell.faces:
                if side.should_skip():
                    continue
                if not side.is_horizontal():
                    yield side

    # --- lookups ----------------------------------------------------------
    def get_cell(self, name) -> GraphCell:
        for cell in self.cells:
            if cell.name == name:
                return cell
        raise ValueError(f"get_cell() no cell with name {name} found")

    def get_external_walls(self) -> list[GraphFace]:
        return [s for s in self._iter_non_horizontal_sides() if s.shared_face_connection is None]

    def get_internal_walls(self) -> list[GraphFace]:
        sides = set()
        for side in self._iter_non_horizontal_sides():
            if side.shared_face_connection is None:
                continue
            f1, f2 = side.shared_face_connection.face1, side.shared_face_connection.face2
            sides.add(f1 if self._priority(f1.parent_cell) > self._priority(f2.parent_cell) else f2)
        return list(sides)

    def get_external_floors(self) -> list[GraphFace]:
        return [s for s in self._iter_horizontal_sides() if s.shared_face_connection is None]

    def get_internal_floors(self) -> list[GraphFace]:
        sides = set()
        up = ada.Direction(0, 0, 1)
        for side in self._iter_horizontal_sides():
            if side.shared_face_connection is None:
                continue
            f1, f2 = side.shared_face_connection.face1, side.shared_face_connection.face2
            if f1 in sides or f2 in sides:
                continue
            sides.add(f1 if f1.normal.is_equal(up) else f2 if f2.normal.is_equal(up) else f1)
        return list(sides)

    def get_external_faces(self) -> list[GraphFace]:
        out = []
        for cell in self.cells:
            for face in cell.faces:
                if face.shared_face_connection is None and not face.should_skip():
                    out.append(face)
        return out

    def get_space_faces(self, space_name) -> list[GraphFace]:
        out = []
        for cell in self.cells:
            if cell.name == space_name:
                out.extend(cell.faces)
        return out

    def get_all_faces(self) -> list[GraphFace]:
        out = []
        out.extend(self.get_external_floors())
        out.extend(self.get_internal_floors())
        out.extend(self.get_external_walls())
        out.extend(self.get_internal_walls())
        return out

    def get_faces_in_box(self, p1, p2) -> list[GraphFace]:
        min_x, max_x = min(p1[0], p2[0]), max(p1[0], p2[0])
        min_y, max_y = min(p1[1], p2[1]), max(p1[1], p2[1])
        min_z, max_z = min(p1[2], p2[2]), max(p1[2], p2[2])
        out = []
        for face in self.get_all_faces():
            c = face.get_centroid()
            if min_x <= c.x <= max_x and min_y <= c.y <= max_y and min_z <= c.z <= max_z:
                out.append(face)
        return out

    def get_sides_by_point_on_and_normal(self, point_on, normal, allow_normal_bidirectional=True) -> list[GraphFace]:
        if not isinstance(point_on, ada.Point):
            point_on = ada.Point(*point_on)
        if not isinstance(normal, ada.Direction):
            normal = ada.Direction(*normal)
        out = []
        for cell in self.cells:
            for side in cell.faces:
                eop = side.get_equation_of_plane()
                if not eop.is_point_in_plane(point_on):
                    continue
                if side.normal.is_equal(normal) or (allow_normal_bidirectional and side.normal.is_equal(-normal)):
                    out.append(side)
        return out

    def iter_groups_by_coplanar_faces(self):
        sorted_sides = sorted(
            self._iter_non_horizontal_sides(),
            key=lambda x: (tuple(np.abs(x.normal)), x.get_normal_position()),
        )
        yield from groupby(sorted_sides, lambda x: (tuple(np.abs(x.normal)), x.get_normal_position()))

    def to_part(self, name: str = "CellGraph") -> "ada.Part":
        p = ada.Part(name)
        for face in self.get_all_faces():
            space_name = face.get_space_name()
            space_parent = p.get_part(space_name, search_all_parts_in_assembly=True)
            if space_parent is None:
                space_parent = p.add_part(ada.Part(space_name))
            space_parent.add_object(ada.Plate.from_3d_points(face.name, face.get_points(), 0.01))
        return p

    # --- constructors -----------------------------------------------------
    @staticmethod
    def from_solids(solids_with_metadata: list[tuple[ShapeHandle, TopologyMetadata]]) -> "CellGraph":
        """Build a graph from already-partitioned cells (solid handle + metadata)."""
        from ada.topology.extraction import GraphCellExtractor

        cells = [GraphCell(handle, [], md) for handle, md in solids_with_metadata]
        return CellGraph(GraphCellExtractor(cells).extract())

    @staticmethod
    def from_faces(faces: list[ShapeHandle], tolerance: float = 1e-4) -> "CellGraph":
        """Partition a face soup into cells, then build the graph.

        Cells get default ``TopologyMetadata`` (named ``Cell0``, ``Cell1`` …).
        Metadata-bearing ingestion (IFC/loft) lives in ``ada.topology.io``.
        """
        from ada.topology.extraction import GraphCellExtractor

        be = active_backend()
        solids = be.make_volumes_from_faces(faces, tolerance=tolerance)
        cells = [GraphCell(s, [], TopologyMetadata(name=f"Cell{i}")) for i, s in enumerate(solids)]
        return CellGraph(GraphCellExtractor(cells).extract())

    @staticmethod
    def from_cell_solids(
        solids_with_metadata: list[tuple[ShapeHandle, TopologyMetadata]],
        merge: bool = True,
        glue: bool = True,
        unify: bool = True,
    ) -> "CellGraph":
        """Build a graph from cell solids (each a solid handle + its metadata).

        When ``merge`` (default), the solids are non-manifold-merged first so
        coincident faces of abutting cells collapse to a single shared face; the
        merged solids are then re-paired to their source metadata by centroid.
        Use ``merge=False`` when the solids already share faces (e.g. came out of
        ``make_volumes_from_faces``).

        When ``unify`` (default), each cell solid has its adjacent coplanar faces
        merged into single faces first. Real geometry often splits a wall into
        several coplanar faces; unifying makes the shared wall a single face so
        the centroid-based shared-face match between adjacent cells is reliable.
        """
        from ada.topology.extraction import GraphCellExtractor

        be = active_backend()
        solids = [s for s, _ in solids_with_metadata]
        metas = [m for _, m in solids_with_metadata]

        if merge and len(solids) > 1:
            merged_solids = be.solids(be.non_manifold_merge(solids, glue=glue))
            meta_by_key = {_round_key(be.center_of_mass(s)): m for s, m in zip(solids, metas)}
            cells = [
                GraphCell(ms, [], meta_by_key.get(_round_key(be.center_of_mass(ms)), TopologyMetadata()))
                for ms in merged_solids
            ]
        else:
            cells = [GraphCell(s, [], m) for s, m in zip(solids, metas)]

        if unify:
            for cell in cells:
                cell.handle = be.unify_coplanar_faces(cell.handle)
        return CellGraph(GraphCellExtractor(cells).extract())

    @staticmethod
    def from_prim_boxes(boxes: "Iterable[ada.PrimBox]") -> "CellGraph":
        """Build a graph from a set of axis-aligned boxes.

        Each box becomes a cell solid (built via the active backend); abutting
        boxes are merged so they share faces. Box ``metadata`` is carried onto
        each cell's ``TopologyMetadata`` (IFC_-prefixed, mirroring IFC ingest).
        """
        pairs = []
        for box in boxes:
            props = {f"IFC_{k}": v for k, v in (box.metadata or {}).items() if v is not None}
            md = TopologyMetadata(name=props.get("IFC_NAME", box.name), properties=props)
            pairs.append((box.solid_occ(), md))
        return CellGraph.from_cell_solids(pairs, merge=True)
