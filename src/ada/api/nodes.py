from __future__ import annotations

from typing import TYPE_CHECKING, Iterable, List, Union

import numpy as np

from ada.base.units import Units
from ada.config import Config, logger
from ada.geom.points import Point

if TYPE_CHECKING:
    from ada import Beam
    from ada.api.containers import Nodes
    from ada.fem import Csys, Elem

numeric = Union[int, float, np.number]


class Node:
    """Base node object

    :param p: 3D coordinates of the node
    :param nid: node id
    :param bc: boundary condition of the node
    """

    def __init__(
        self, p: Iterable[numeric, numeric, numeric] | Point, nid=None, r=None, parent=None, units=Units.M, refs=None
    ):
        self._id = nid
        self.p: Point = Point(*p) if not isinstance(p, Point) else p
        if len(self.p) != 3:
            raise ValueError("Node object must have exactly 3 coordinates (x, y, z).")

        self._r = r
        self._parent = parent
        self._units = units
        self._refs = [] if refs is None else refs
        self._precision = Config().general_precision

    @property
    def id(self) -> int:
        return self._id

    @id.setter
    def id(self, value: int):
        self._id = value

    @property
    def x(self):
        return self.p[0]

    @property
    def y(self):
        return self.p[1]

    @property
    def z(self):
        return self.p[2]

    @property
    def r(self) -> float:
        return self._r

    def p_roundoff(self, scale_factor: Union[int, float] = 1, precision: int = None) -> None:
        from ada.core.utils import roundoff

        if precision is None:
            precision = self._precision

        self.p = np.array([roundoff(scale_factor * x, precision=precision) for x in self.p])

    def add_obj_to_refs(self, item) -> None:
        if item not in self.refs:
            self.refs.append(item)
        else:
            logger.debug(f"Item {item} is already in node refs {self}")

    def remove_obj_from_refs(self, item) -> None:
        if item in self.refs:
            self.refs.remove(item)
        else:
            logger.debug(f"Item {item} is not in node refs {self}")

    @property
    def units(self):
        return self._units

    @units.setter
    def units(self, value):
        if isinstance(value, str):
            value = Units.from_str(value)
        if value != self._units:
            scale_factor = Units.get_scale_factor(self._units, value)

            self.p_roundoff(scale_factor)

            if self._r is not None:
                self._r *= scale_factor
            self._units = value

    @property
    def parent(self):
        return self._parent

    @parent.setter
    def parent(self, value):
        self._parent = value

    @property
    def refs(self) -> List[Union[Elem, Beam, Csys]]:
        return self._refs

    @property
    def has_refs(self) -> bool:
        """Returns if node is valid, i.e. has objects in refs"""
        return len(self.refs) > 0

    def get_main_node_at_point(self) -> Node:
        nodes = self.sort_by_refs_at_point()
        (nearest_node,) = sort_nodes_by_distance(self, nodes)
        return nearest_node

    def sort_by_refs_at_point(self) -> list[Node]:
        nodes = list(filter(lambda n: n.has_refs, self.parent.nodes.get_by_volume(self)))
        if len(nodes) > 0:
            return sorted(nodes, key=lambda n: len(n.refs), reverse=True)
        else:
            return [self]

    def __len__(self):
        return len(self.p)

    def __getitem__(self, index):
        return self.p[index]

    def __gt__(self, other):
        return tuple(self.p) > tuple(other.p)

    def __lt__(self, other):
        return tuple(self.p) < tuple(other.p)

    def __ge__(self, other):
        return tuple(self.p) >= tuple(other.p)

    def __le__(self, other):
        return tuple(self.p) <= tuple(other.p)

    def __eq__(self, other: Node):
        if not isinstance(other, Node):
            return NotImplemented
        return (*self.p, self.id) == (*other.p, other.id)

    def __ne__(self, other: Node):
        if not isinstance(other, Node):
            return NotImplemented
        return (*self.p, self.id) != (*other.p, other.id)

    def __hash__(self):
        return hash((*self.p, self.id))

    def __repr__(self):
        return f"{self.__class__.__name__}([{self.x}, {self.y}, {self.z}], {self.id})"


def get_singular_node_by_volume(nodes: Nodes, p: np.ndarray, tol=Config().general_point_tol) -> Node:
    """Returns existing node within the volume, or creates and returns a new Node at the point"""
    nds = nodes.get_by_volume(p, tol=tol)
    if len(nds) > 0:
        node, *other_nodes = nds
        if len(other_nodes) > 0:
            logger.warning(f"More than 1 node within point {p}, other nodes: {other_nodes}. Returns node {node}")
        return node
    else:
        return Node(p)


def sort_nodes_by_distance(point: Union[Node, np.ndarray], nodes: list[Node]) -> list[Node]:
    from ada.core.vector_utils import vector_length

    if isinstance(point, Node):
        point = point.p
    return sorted(nodes, key=lambda x: vector_length(x.p - point))


def replace_nodes_by_tol(nodes, decimals=0, tol=Config().general_point_tol):
    def rounding(vec, decimals_):
        return np.around(vec, decimals=decimals_)

    def n_is_most_precise(n, nearby_nodes_, decimals_=0):
        most_precise = [np.array_equal(n.p, rounding(n.p, decimals_)) for n in [node] + nearby_nodes_]

        if most_precise[0] and not np.all(most_precise[1:]):
            return True
        elif not most_precise[0] and np.any(most_precise[1:]):
            return False
        elif decimals_ == 10:
            logger.error(f"Recursion started at 0 decimals, but are now at {decimals_} decimals. Will proceed with n.")
            return True
        else:
            return n_is_most_precise(n, nearby_nodes_, decimals_ + 1)

    for node in nodes:
        nearby_nodes = list(filter(lambda x: x != node, nodes.get_by_volume(node.p, tol=tol)))
        if nearby_nodes and n_is_most_precise(node, nearby_nodes, decimals):
            for nearby_node in nearby_nodes:
                replace_node(nearby_node, node)


def replace_node(old_node: Node, new_node: Node) -> None:
    """Replaces old node with a new. The refs in old node is cleared, and added to new node ref"""

    from ada import Beam
    from ada.api.beams.helpers import updating_nodes
    from ada.fem import Elem

    for obj in old_node.refs.copy():
        obj: Union[Beam, Csys, Elem]
        if isinstance(obj, Beam):
            updating_nodes(obj, old_node, new_node)
        elif isinstance(obj, Elem):
            obj.updating_nodes(old_node, new_node)
        else:
            pass

        old_node.remove_obj_from_refs(obj)
        new_node.add_obj_to_refs(obj)

        logger.debug(f"{old_node} exchanged with {new_node} --> {obj}")
