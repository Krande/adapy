from __future__ import annotations

import reprlib
from itertools import chain
from typing import TYPE_CHECKING, Iterable, List

from ada.api.containers.base import BaseCollections
from ada.api.nodes import Node
from ada.config import Config, logger
from ada.core.utils import Counter

if TYPE_CHECKING:
    from ada.api.connections import JointBase


class Connections(BaseCollections):
    _counter = Counter(1, "C")

    def __init__(self, connections: Iterable[JointBase] = None, parent=None):
        connections = [] if connections is None else connections
        super().__init__(parent)
        self._connections = connections
        self._initialize_connection_data()

    def _initialize_connection_data(self):
        from ada.api.containers.nodes import Nodes

        self._dmap = {j.name: j for j in self._connections}
        self._joint_centre_nodes = Nodes([c.centre for c in self._connections])
        self._nmap = {self._joint_centre_nodes.index(c.centre): c for c in self._connections}

    @property
    def connections(self) -> List[JointBase]:
        return self._connections

    @connections.setter
    def connections(self, value: List[JointBase]):
        self._connections = value
        self._initialize_connection_data()

    @property
    def joint_centre_nodes(self):
        return self._joint_centre_nodes

    def __contains__(self, item):
        return item.id in self._dmap.keys()

    def __len__(self):
        return len(self._connections)

    def __iter__(self) -> Iterable[JointBase]:
        return iter(self._connections)

    def __getitem__(self, index):
        result = self._connections[index]
        return Connections(result) if isinstance(index, slice) else result

    def __eq__(self, other: Connections):
        if not isinstance(other, Connections):
            return NotImplemented
        return self._connections == other._connections

    def __ne__(self, other: Connections):
        if not isinstance(other, Connections):
            return NotImplemented
        return self._connections != other._connections

    def __add__(self, other: Connections):
        return Connections(chain(self._connections, other._connections))

    def __repr__(self):
        rpr = reprlib.Repr()
        rpr.maxlist = 8
        rpr.maxlevel = 1
        return f"Connections({rpr.repr(self._connections) if self._connections else ''})"

    def get_from_name(self, name: str):
        result = self._dmap.get(name, None)
        if result is None:
            logger.error(f'No Joint with the name "{name}" found within this connection object')
        return result

    def add(self, joint: JointBase, point_tol=Config().general_point_tol):
        if joint.name is None:
            raise Exception("Name is not allowed to be None.")

        if joint.name in self._dmap.keys():
            raise ValueError("Joint Exists with same name")

        new_node = Node(joint.centre)
        node = self._joint_centre_nodes.add(new_node, point_tol=point_tol)
        if node != new_node:
            return self._nmap[node]
        else:
            self._nmap[node] = joint
        joint.parent = self
        self._dmap[joint.name] = joint
        self._connections.append(joint)

    def remove(self, joint: JointBase):
        if joint.name in self._dmap.keys():
            self._dmap.pop(joint.name)
        if joint in self._connections:
            self._connections.pop(self._connections.index(joint))
        if joint.centre in self._nmap.keys():
            self._nmap.pop(joint.centre)

    def find(self, out_of_plane_tol=0.1, joint_func=None, point_tol=Config().general_point_tol):
        """
        Find all connections between beams in all parts using a simple clash check.

        :param out_of_plane_tol:
        :param joint_func: Pass a function for mapping the generic Connection classes to a specific reinforced Joints
        :param point_tol:
        """
        from ada.api.connections import JointBase
        from ada.api.containers.nodes import Nodes
        from ada.core.clash_check import are_beams_connected

        ass = self._parent.get_assembly()
        bm_res = ass.beam_clash_check()

        nodes = Nodes()
        nmap = dict()

        for bm1_, beams_ in bm_res:
            are_beams_connected(bm1_, beams_, out_of_plane_tol, point_tol, nodes, nmap)

        for node, mem in nmap.items():
            if joint_func is not None:
                joint = joint_func(next(self._counter), mem, node.p, parent=self)
                if joint is None:
                    continue
            else:
                joint = JointBase(next(self._counter), mem, node.p, parent=self)

            self.add(joint, point_tol=point_tol)

        logger.info(f"Connection search finished. Found a total of {len(self._connections)} connections")
