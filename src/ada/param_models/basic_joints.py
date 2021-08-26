import logging

from ada import JointBase
from ada.concepts.structural import Beam
from ada.core.clash_check import beam_cross_check


def joint_map(name, members, centre):
    """
    :param name: Name of joint
    :param members: Number of members
    :param centre: Centre of joint
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

    joints = [JointB, JointI90deg]

    for j in joints:
        if eval_joint_req(j):
            return j(name, members, centre)

    member_types = [m.section.type for m in members]
    logging.error(f'Unable to find matching Joint using joint map for members "{member_types}"')
    return None


class JointI90deg(JointBase):
    mem_types = ["Column", "Girder"]
    beamtypes = ["IPE", "IPE"]
    num_mem = 2

    def __init__(self, name, members, centre):
        super(JointI90deg, self).__init__(name, members, centre)

        column = None
        gi1 = None
        for m in members:
            if m.member_type == "Column":
                column = m
            else:
                gi1 = m

        self._cut_intersecting_member(gi1, column)

        center, s, t = beam_cross_check(column, gi1)
        self.adjust_column(column, gi1, s)

    def adjust_column(self, column: Beam, gi1: Beam, s):
        dist = gi1.section.h / 2
        xvec = column.xvec

        adjust_vec = 1 if s == 0.0 else -1
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


class JointB(JointBase):
    mem_types = ["Column", "Girder", "Girder"]
    beamtypes = ["IG", "IG", "IG"]
    num_mem = 3

    def __init__(self, name, members, centre):
        super(JointB, self).__init__(name, members, centre)

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

    def adjust_column(self, column: Beam, gi1: Beam, gi2: Beam, s):
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
