from typing import Union

from ada import Beam
from ada.api.connections import JointBase, JointReqChecker
from ada.config import get_logger
from ada.core.clash_check import beam_cross_check

logger = get_logger()


def eval_joint_req(joint: JointBase, intersecting_members):
    jrc = JointReqChecker(intersecting_members, joint)
    return jrc.eval_joint_req()


def joint_map(joint_name, intersecting_members, centre, parent=None) -> Union[JointBase, None]:
    joints = [JointB, JointIXZ]

    for joint in joints:
        if eval_joint_req(joint, intersecting_members):
            return joint(joint_name, intersecting_members, centre, parent=parent)

    member_types = [m.section.type for m in intersecting_members]
    logger.debug(f'Unable to find matching Joint using joint map for members "{member_types}"')
    return None


class JointIXZ(JointBase):
    mem_types = ["Column|Brace", "Girder"]
    beamtypes = ["IPE", "IPE"]
    num_mem = 2

    def __init__(self, name, members, centre, parent=None):
        super(JointIXZ, self).__init__(name, members, centre, parent=parent)

        non_girder = None
        gi1 = None
        for m in members:
            if m.member_type == "Column" or m.member_type == "Brace":
                non_girder = m
            else:
                gi1 = m

        self._cut_intersecting_member(gi1, non_girder)
        center, s, t = beam_cross_check(non_girder, gi1)
        self.adjust_column(non_girder, gi1, s)

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

    def __init__(self, name, members, centre, parent=None):
        super(JointB, self).__init__(name, members, centre, parent=parent)

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
