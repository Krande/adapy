from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, List

from ada.api.containers import Beams, Connections
from ada.base.physical_objects import BackendGeom
from ada.config import logger

if TYPE_CHECKING:
    from ada import Beam, Node


@dataclass
class JointReqChecker:
    intersecting_members: List[Beam]
    joint: JointBase

    @property
    def is_equal_num(self):
        return len(self.intersecting_members) == self.joint.num_mem

    @property
    def correct_member_types(self):
        mem_types = [m.member_type for m in self.intersecting_members]
        req_types = [mt.split("|") for mt in self.joint.mem_types]
        for m in req_types:
            found = False
            for sub_m in m:
                if sub_m in mem_types:
                    found = True
                    mem_types.pop(mem_types.index(sub_m))
                    break
            if found is False:
                return False

        return True

    def eval_joint_req(self, silent=False):
        is_equal_num = self.is_equal_num
        correct_mem_types = self.correct_member_types

        res = all([is_equal_num, correct_mem_types])
        if res is True and silent is False and self.joint.__class__.__name__ != "ABCMeta":
            mtyp = self.joint.mem_types
            logger.info(f'Joint match for "{self.joint.__class__.__name__}", Req: types, "{mtyp}"')
        return res


class JointBase(BackendGeom, ABC):
    beamtypes: list
    mem_types: list
    num_mem: int

    def __init__(self, name, members: List[Beam], centre: Any[float], parent: Connections = None):
        super(JointBase, self).__init__(name, parent=parent)
        self._init_check(members)
        self._centre = centre
        self._beams = Beams(members)
        self._main_mem = self._get_landing_member(members)

        for m in members:
            m.connection_props.connected_to.append(self)
            m._ifc_elem = None

        if parent is not None:
            parent.parent.add_group(f"{name}_joint", members)

    def _init_check(self, members):
        if self.__class__.__name__ == "JointBase":
            return None

        if self.num_mem != len(members):
            raise ValueError(f"This joint only supports {self.num_mem} members")
        jrc = JointReqChecker(members, self)
        if jrc.eval_joint_req() is False:
            raise ValueError(f"Not all Pre-requisite member types {self.mem_types} are found for JointB")

    def _cut_intersecting_member(self, mem_base: Beam, mem_incoming: Beam):
        from ada import PrimBox

        p1, p2 = mem_base.bbox().minmax
        mem_incoming.add_boolean(PrimBox(f"{self.name}_neg", p1, p2))

    def _get_landing_member(self, members) -> Beam:
        member_types = [m.member_type for m in members]
        if member_types.count("Column") >= 1:
            return members[member_types.index("Column")]
        elif member_types.count("Girder") >= 1:
            return members[member_types.index("Girder")]
        else:
            return members[0]

    def get_all_physical_objects(self):
        return self.beams

    @property
    def main_mem(self) -> Beam:
        return self._main_mem

    @property
    def beams(self) -> Beams:
        return self._beams

    @property
    def centre(self) -> Node:
        return self._centre

    def __repr__(self):
        return f'{self.__class__.__name__}("{self.name}", members:{len(self.beams)})'


class Connection(BackendGeom):
    def __init__(self, name: str, parent: Connections = None):
        super(Connection, self).__init__(name, parent=parent)
