import numpy as np

from ada.base import Backend
from ada.concepts.containers import Beams
from ada.concepts.levels import Part
from ada.concepts.primitives import PrimBox
from ada.concepts.structural import Beam


class JointBase(Part):
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

        mem_types = [m for m in self.mem_types]

        for m in members:
            if m.member_type in mem_types:
                mem_types.pop(mem_types.index(m.member_type))

        if len(mem_types) != 0:
            raise ValueError(f"Not all Pre-requisite member types {self.mem_types} are found for JointB")

    def _cut_intersecting_member(self, mem_base, mem_incoming):
        """

        :param mem_base:
        :param mem_incoming:
        :type mem_base: ada.Beam
        :type mem_incoming: ada.Beam
        """
        h_vec = np.array(mem_incoming.up) * mem_incoming.section.h * 2
        p1, p2 = mem_base.bbox
        p1 = np.array(p1) - h_vec
        p2 = np.array(p2) + h_vec
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
    def centre(self):
        return self._centre

    def __repr__(self):
        return f'{self.__class__.__name__}("{self.name}", members:{len(self.beams)})'


class Bolts(Backend):
    """

    TODO: Create a bolt class based on the IfcMechanicalFastener concept.

    https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/schema/ifcsharedcomponentelements/lexical/ifcmechanicalfastener.htm

    Which in turn should likely be inside another element components class

    https://standards.buildingsmart.org/IFC/RELEASE/IFC4_1/FINAL/HTML/schema/ifcsharedcomponentelements/lexical/ifcelementcomponent.htm

    """

    def __init__(self, name, p1, p2, normal, members, parent=None):
        super(Bolts, self).__init__(name, parent=parent)


class Weld(Backend):
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
