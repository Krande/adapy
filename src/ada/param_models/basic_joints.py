import logging

import numpy as np

from ada import Part, PrimBox
from ada.core.containers import Beams
from ada.core.utils import beam_cross_check


def joint_map(name, members):
    """
    :param name: Name of joint
    :param members: Number of members
    :return: Joint
    """

    def mem_type_check(joint):
        mem_types = [m for m in joint.mem_types]

        for m in members:
            if m.member_type in mem_types:
                mem_types.pop(mem_types.index(m.member_type))

        if len(mem_types) != 0:
            return False
        else:
            return True

    def eval_joint_req(joint):
        if len(members) == joint.num_mem and mem_type_check(joint) is True:
            return True
        else:
            return False

    joints = [JointB]

    for joint in joints:
        if eval_joint_req(joint):
            return joint(name, members)
    else:
        member_types = [m.section.type for m in members]
        logging.error(f'Unable to find matching Joint using joint map for members "{member_types}"')
        return None


class JointBase(Part):
    beamtypes: list
    mem_types: list
    num_mem: int

    def __init__(self, name, members):
        super(JointBase, self).__init__(name)
        self._init_check(members)
        self._beams = Beams(members)
        for m in members:
            m._ifc_elem = None

    def _init_check(self, members):
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


class JointB(JointBase):
    mem_types = ["Column", "Girder", "Girder"]
    beamtypes = ["IG", "IG", "IG"]
    num_mem = 3

    def __init__(self, name, members):
        super(JointB, self).__init__(name, members)

        column = None
        gi1 = None
        gi2 = None
        for m in members:
            if m.member_type == "Column":
                column = m
            elif m.member_type == "Girder" and gi1 is None:
                gi1 = m
            else:
                gi2 = m

        self._cut_intersecting_member(column, gi1)
        self._cut_intersecting_member(column, gi2)

        center, s, t = beam_cross_check(column, gi1)
        self.adjust_column(column, gi1, gi2, s)

    def adjust_column(self, column, gi1, gi2, s):
        """

        :param column:
        :param gi1:
        :param gi2:
        :param s:
        :type column: ada.Beam
        :type gi1: ada.Beam
        :type gi2: ada.Beam
        :return:
        """

        dist = max(gi1.section.h, gi2.section.h) / 2
        xvec = column.xvec

        adjust_vec = 1 if s == 1.0 else -1
        adjust_col_end = adjust_vec * xvec * dist
        if adjust_vec == 1:
            if column.e2 is None:
                column.e2 = adjust_col_end
            else:
                column.e2 += adjust_col_end
        else:
            if column.e1 is None:
                column.e1 = adjust_col_end
            else:
                column.e1 += adjust_col_end
