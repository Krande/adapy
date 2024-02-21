from itertools import groupby
from typing import Dict, List, Union

from ada.fem import FEM, Bc, Constraint, FemSet
from ada.fem.formats.utils import str_to_int

from . import cards


def get_constraints(bulk_str, fem: FEM) -> Dict[str, Constraint]:
    con_map = [m.groupdict() for m in cards.re_bldep.finditer(bulk_str)]
    con_map.sort(key=lambda x: x["master"])
    constraints: Dict[str, Constraint] = {}
    for m, d in groupby(con_map, key=lambda x: x["master"]):
        c = grab_constraint(m, d, fem)
        constraints[c.name] = c
    return constraints


def grab_constraint(master, data, fem: FEM) -> Constraint:
    m = str_to_int(master)
    m_set = FemSet(f"co{m}_m", [fem.nodes.from_id(m)], "nset")
    slaves = []
    for d in data:
        s = str_to_int(d["slave"])
        slaves.append(fem.nodes.from_id(s))
    s_set = FemSet(f"co{m}_s", slaves, "nset")
    fem.add_set(m_set)
    fem.add_set(s_set)
    return Constraint(f"co{m}", Constraint.TYPES.COUPLING, m_set, s_set, parent=fem)


def get_bcs(bulk_str, fem: FEM) -> List[Bc]:
    return list(filter(lambda x: x is not None, [grab_bc(m, fem) for m in cards.re_bnbcd.finditer(bulk_str)]))


def grab_bc(match, fem: FEM) -> Union[Bc, None]:
    d = match.groupdict()
    node = fem.nodes.from_id(str_to_int(d["nodeno"]))
    for constraint in fem.constraints.values():
        if node in constraint.m_set.members:
            return None
        if node in constraint.s_set.members:
            return None

    fem_set = fem.sets.add(FemSet(f"bc{node.id}_set", [node], "nset"))
    dofs = []
    for i, c in enumerate(d["content"].replace("\n", "").split()):
        bc_sestype = str_to_int(c.strip())
        if bc_sestype in [0, 4]:
            continue
        dofs.append(i + 1)
    bc = Bc(f"bc{node.id}", fem_set, dofs, parent=fem)
    node.bc = bc
    return bc
