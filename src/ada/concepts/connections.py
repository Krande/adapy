from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from typing import List

from ada.base.physical_objects import BackendGeom
from ada.concepts.containers import Beams
from ada.concepts.primitives import PrimBox
from ada.concepts.structural import Beam


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
            print(f'Joint match for "{self.joint.__class__.__name__}", Req: types, "{mtyp}"')
        return res


class JointBase(BackendGeom, ABC):
    beamtypes: list
    mem_types: list
    num_mem: int

    def __init__(self, name, members, centre):
        super(JointBase, self).__init__(name)
        self._init_check(members)
        self._centre = centre
        self._beams = Beams(members)
        self._main_mem = self._get_landing_member(members)

        for m in members:
            m.connected_to.append(self)
            m._ifc_elem = None

    def _init_check(self, members):
        if self.__class__.__name__ == "JointBase":
            return None

        if self.num_mem != len(members):
            raise ValueError(f"This joint only supports {self.num_mem} members")
        jrc = JointReqChecker(members, self)
        if jrc.eval_joint_req() is False:
            raise ValueError(f"Not all Pre-requisite member types {self.mem_types} are found for JointB")

    def _cut_intersecting_member(self, mem_base: Beam, mem_incoming: Beam):
        p1, p2 = mem_base.bbox
        mem_incoming.add_penetration(PrimBox(f"{self.name}_neg", p1, p2))

    def _get_landing_member(self, members) -> Beam:
        member_types = [m.member_type for m in members]
        if member_types.count("Column") >= 1:
            return members[member_types.index("Column")]
        elif member_types.count("Girder") >= 1:
            return members[member_types.index("Girder")]
        else:
            return members[0]

    @property
    def main_mem(self) -> Beam:
        return self._main_mem

    @property
    def beams(self) -> Beams:
        return self._beams

    @property
    def centre(self):
        return self._centre

    def __repr__(self):
        return f'{self.__class__.__name__}("{self.name}", members:{len(self.beams)})'


class Bolts(BackendGeom):
    """

    TODO: Create a bolt class based on the IfcMechanicalFastener concept.

    https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/schema/ifcsharedcomponentelements/lexical/ifcmechanicalfastener.htm

    Which in turn should likely be inside another element components class

    https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/schema/ifcsharedcomponentelements/lexical/ifcelementcomponent.htm

    """

    def __init__(self, name, p1, p2, normal, members, parent=None):
        super(Bolts, self).__init__(name, parent=parent)


class Weld(BackendGeom):
    """
    TODO: Create a weld class based on the IfcFastener concept.

    https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/schema/ifcsharedcomponentelements/lexical/ifcfastener.htm
    """

    def __init__(self, name, p1, p2, normal, members, parent=None):
        super(Weld, self).__init__(name, parent=parent)
        self._p1 = p1
        self._p2 = p2
        self._normal = normal
        self._members = members

    def _generate_ifc_elem(self):
        """



        :return:
        """
        if self.parent is None:
            raise ValueError("Parent cannot be None for IFC export")

        # a = self.parent.get_assembly()
        # f = a.ifc_file

        # context = f.by_type("IfcGeometricRepresentationContext")[0]
        # owner_history = f.by_type("IfcOwnerHistory")[0]
        # parent = self.parent.ifc_elem

        # ifc_fastener = f.createIfcFastener()
