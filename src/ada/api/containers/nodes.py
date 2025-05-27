from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from itertools import chain
from operator import attrgetter
from typing import TYPE_CHECKING, Dict, Iterable, List

import numpy as np

from ada.api.exceptions import DuplicateNodes
from ada.api.nodes import Node
from ada.config import Config, logger
from ada.core.vector_utils import points_in_cylinder, vector_length

if TYPE_CHECKING:
    from ada.fem.results.common import FemNodes


class Nodes:
    def __init__(
        self,
        nodes: Iterable[Node] | None = None,
        parent=None,
        from_np_array: np.ndarray | None = None,
    ):
        self._parent = parent
        self._bbox = None
        self._maxid = 0

        # spatial grid setup for fast neighbor lookups
        config = Config()
        self._point_tol = config.general_point_tol
        self._cell_size = self._point_tol
        self._grid: defaultdict[tuple[int, int, int], list[Node]] = defaultdict(list)

        # initialize nodes list
        if from_np_array is not None:
            self._array = from_np_array
            nodes_list = self._np_array_to_nlist(from_np_array)
        else:
            nodes_list = [] if nodes is None else list(nodes)

        # ensure unique
        if len(set(nodes_list)) != len(nodes_list):
            raise DuplicateNodes("Duplicate Nodes not allowed in a Nodes object")

        self._nodes: List[Node] = nodes_list
        self._idmap: Dict[int, Node] = {}

        if self._nodes:
            self._sort()
            self._maxid = max(self._idmap.keys())
            self._bbox = self._get_bbox()
            # populate grid
            for n in self._nodes:
                self._add_to_grid(n)

    def _voxel_key(self, p: np.ndarray) -> tuple[int, int, int]:
        return tuple((p // self._cell_size).astype(int))

    def _add_to_grid(self, node: Node) -> None:
        key = self._voxel_key(node.p)
        self._grid[key].append(node)

    def _sort(self) -> None:
        self._nodes.sort(key=attrgetter("x", "y", "z"))
        try:
            self._idmap = {n.id: n for n in sorted(self._nodes, key=attrgetter("id"))}
        except TypeError as e:
            raise TypeError(e)

    def renumber(self, start_id: int = 1, renumber_map: dict[int, int] | None = None) -> None:
        if renumber_map is not None:
            self._renumber_from_map(renumber_map)
        else:
            self._renumber_linearly(start_id)

        self._sort()
        self._maxid = max(self._idmap.keys()) if self._nodes else 0
        self._bbox = self._get_bbox() if self._nodes else None

    def _renumber_linearly(self, start_id: int) -> None:
        for idx, n in enumerate(sorted(self._nodes, key=attrgetter("id")), start=start_id):
            if n.id != idx:
                n.id = idx

    def _renumber_from_map(self, renumber_map: dict[int, int]) -> None:
        for n in sorted(self._nodes, key=attrgetter("id")):
            n.id = renumber_map[n.id]

    def _np_array_to_nlist(self, np_array: np.ndarray) -> List[Node]:
        return [Node(row[1:], int(row[0]), parent=self._parent) for row in np_array]

    def to_np_array(self, include_id: bool = False) -> np.ndarray:
        if include_id:
            return np.array([(n.id, *n.p) for n in self._nodes])
        return np.array([n.p for n in self._nodes])

    def to_fem_nodes(self) -> FemNodes:
        from ada.fem.results.common import FemNodes

        node_refs = self.to_np_array(include_id=True)
        identifiers = node_refs[:, 0]
        coords = node_refs[:, 1:]
        return FemNodes(coords, identifiers)

    def __contains__(self, item: Node) -> bool:
        return item in self._nodes

    def __len__(self) -> int:
        return len(self._nodes)

    def __iter__(self) -> Iterable[Node]:
        return iter(self._nodes)

    def __getitem__(self, index: int | slice) -> Node | "Nodes":
        result = self._nodes[index]
        return Nodes(result) if isinstance(index, slice) else result

    def __eq__(self, other) -> bool:
        if not isinstance(other, Nodes):
            return NotImplemented
        return self._nodes == other._nodes

    def __ne__(self, other) -> bool:
        if not isinstance(other, Nodes):
            return NotImplemented
        return self._nodes != other._nodes

    def __add__(self, other: "Nodes") -> "Nodes":
        for n in other.nodes:
            n.parent = self.parent
        return Nodes(chain(self._nodes, other.nodes))

    def __repr__(self) -> str:
        return f"Nodes({len(self._nodes)}, min_id: {self.min_nid}, max_id: {self.max_nid})"

    def index(self, item: Node) -> int:
        idx = bisect_left(self._nodes, item)
        if idx < len(self._nodes) and self._nodes[idx] == item:
            return idx
        raise ValueError(f"{item!r} not found")

    def count(self, item: Node) -> int:
        return int(item in self)

    def move(self, move: Iterable[float] | None = None, rotate=None) -> None:
        if rotate is not None:
            origin = np.array(rotate.origin)
            rot_mat = rotate.to_rot_matrix()
            vectors = np.array([n.p - origin for n in self._nodes])
            transformed = vectors @ rot_mat.T
            for n, new_p in zip(self._nodes, transformed):
                n.p = new_p + origin

        if move is not None:
            mv = np.array(move)
            for n in self._nodes:
                n.p = n.p + mv

        self._sort()

    def from_id(self, nid: int) -> Node:
        if nid not in self._idmap:
            raise ValueError(f'The node id "{nid}" is not found')
        return self._idmap[nid]

    def _get_bbox(self) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
        if not self._nodes:
            raise ValueError("No Nodes are found")
        xs = [n.x for n in self._nodes]
        ys = [n.y for n in self._nodes]
        zs = [n.z for n in self._nodes]
        return (min(xs), max(xs)), (min(ys), max(ys)), (min(zs), max(zs))

    @property
    def dmap(self) -> Dict[int, Node]:
        return self._idmap

    def bbox(self) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]]:
        if self._bbox is None:
            self._bbox = self._get_bbox()
        return self._bbox

    def vol_cog(self) -> tuple[float, float, float]:
        bx, by, bz = self.bbox()
        return ((bx[0] + bx[1]) / 2, (by[0] + by[1]) / 2, (bz[0] + bz[1]) / 2)

    @property
    def max_nid(self) -> int:
        return max(self._idmap.keys()) if self._idmap else 0

    @property
    def min_nid(self) -> int:
        return min(self._idmap.keys()) if self._idmap else 0

    @property
    def nodes(self) -> List[Node]:
        return self._nodes

    def get_by_volume(
        self,
        p: tuple[float, float, float] | None = None,
        vol_box: tuple[float, float, float] | None = None,
        vol_cyl: tuple[float, float, float] | None = None,
        tol: float | None = None,
        single_member: bool = False,
    ) -> List[Node] | Node:
        if tol is None:
            tol = self._point_tol
        if p is not None:
            p_arr = np.array(p)
        else:
            p_arr = None

        if p_arr is not None and vol_box is None and vol_cyl is None:
            vol_min = p_arr - tol
            vol_max = p_arr + tol
        elif vol_box is not None and p_arr is not None:
            vol_min = np.array(p)
            vol_max = np.array(vol_box)
        elif vol_cyl is not None and p_arr is not None:
            r, h, t = vol_cyl
            vol_min = np.array([p_arr[0] - r - tol, p_arr[1] - r - tol, p_arr[2] - tol])
            vol_max = np.array([p_arr[0] + r + tol, p_arr[1] + r + tol, p_arr[2] + tol + h])
        else:
            raise Exception("No valid search input provided. None is returned")

        # 1) x-range slice
        xmin = bisect_left(self._nodes, Node(vol_min))
        xmax = bisect_right(self._nodes, Node(vol_max))
        candidates = self._nodes[xmin:xmax]

        # 2) filter y/z
        filtered = [n for n in candidates if vol_min[1] <= n.y <= vol_max[1] and vol_min[2] <= n.z <= vol_max[2]]

        # 3) cylinder-specific
        if vol_cyl is not None:
            pt1 = p_arr + np.array([0, 0, -h])
            pt2 = p_arr + np.array([0, 0, h])
            cyl_res = []
            for n in filtered:
                if t == r:
                    if points_in_cylinder(pt1, pt2, r, n.p):
                        cyl_res.append(n)
                else:
                    inside_outer = points_in_cylinder(pt1, pt2, r + t, n.p)
                    inside_inner = points_in_cylinder(pt1, pt2, r - t, n.p)
                    if inside_outer and not inside_inner:
                        cyl_res.append(n)
            result = cyl_res
        else:
            result = filtered

        if not result:
            logger.info(f"No nodes found in volume {vol_min}, {vol_max}, tol={tol}")
            return result

        if single_member:
            if len(result) != 1:
                logger.warning(f"Returning first of {len(result)} results; single_member=True")
            return result[0]

        return result

    def add(
        self,
        node: Node,
        point_tol: float = None,
        allow_coincident: bool = False,
    ) -> Node:
        # fast duplicate detection via spatial grid
        tol = point_tol if point_tol is not None else self._point_tol
        if self._nodes and not allow_coincident:
            base_key = self._voxel_key(node.p)
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    for dz in (-1, 0, 1):
                        for other in self._grid.get((base_key[0] + dx, base_key[1] + dy, base_key[2] + dz), []):
                            if vector_length(other.p - node.p) < tol:
                                logger.debug(f"Using existing node id {other.id} within tol={tol}")
                                return other

        # assign new ID if needed
        new_id = (self._maxid + 1) if self._nodes else 1
        if node.id is None or node.id in self._idmap:
            node.id = new_id

        # sorted insert
        idx = bisect_left(self._nodes, node)
        self._nodes.insert(idx, node)
        self._idmap[node.id] = node
        self._bbox = None
        self._maxid = max(self._maxid, node.id)
        self._add_to_grid(node)

        if node.parent is None:
            node.parent = self.parent

        return node

    def remove(self, nodes: Node | Iterable[Node]) -> None:
        nodes_list = [nodes] if isinstance(nodes, Node) else list(nodes)
        for n in nodes_list:
            if n.id in self._idmap:
                self._idmap.pop(n.id)
            else:
                logger.error(f"'{n.id}' not found in container")
        self._nodes = list(self._idmap.values())
        self.renumber()

    def remove_standalones(self) -> None:
        self.remove([n for n in self._nodes if not n.has_refs])

    def merge_coincident(self, tol: float = None) -> None:
        tol = tol if tol is not None else self._point_tol
        for n in list(self._nodes):
            if not n.has_refs:
                continue
            duplicates = sorted(
                [m for m in self.get_by_volume(tuple(n.p), tol=tol) if m.id != n.id],
                key=lambda x: len(x.refs),
            )
            if duplicates:
                primary = max([n] + duplicates, key=lambda x: len(x.refs))
                for dup in duplicates:
                    from ada.fem.utils import replace_node

                    replace_node(dup, primary)
                    self.remove(dup)
        self._sort()

    def rounding_node_points(self, precision: int = Config().general_precision) -> None:
        for n in self._nodes:
            n.p_roundoff(precision=precision)

    @property
    def parent(self):
        return self._parent

    @parent.setter
    def parent(self, value):
        self._parent = value
