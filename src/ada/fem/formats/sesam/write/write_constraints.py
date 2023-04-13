from ada import FEM
from ada.config import logger
from ada.fem import Constraint
from ada.fem.common import LinDep

from .write_utils import write_ff


def constraint_str(fem: FEM) -> str:
    out_str = ""
    for constraint in fem.constraints.values():
        if constraint.type == constraint.TYPES.COUPLING:
            out_str += write_coupling(constraint)
        else:
            raise logger.error(f'Constraint type "{constraint.type}" is not yet supported')

    return out_str


def write_coupling(constraint: Constraint) -> str:
    out_str = ""
    master = constraint.m_set.members[0]
    master_id = master.id
    for node in constraint.s_set.members:
        slave_id = node.id
        res = LinDep(master.p, node.p)
        lin_deps = [(slave_id, master_id, 3, 9)]
        for lin_dep_rel in res.to_integer_list():
            lin_deps.append(tuple(list(lin_dep_rel) + [0.0]))
        out_str += write_ff("BLDEP", lin_deps)

    return out_str
