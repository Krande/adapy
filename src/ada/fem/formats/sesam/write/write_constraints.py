from ada import FEM
from ada.fem import Constraint
from ada.fem.common import LinDep

from .write_utils import write_ff


def constraint_str(fem: FEM) -> str:
    out_str = ""
    for constraint in fem.constraints.values():
        # A rigid body links every slave node rigidly to the master (reference) node — the
        # same kinematic relation Sesam expresses with BLDEP linear-dependency cards, so it
        # writes identically to a coupling.
        if constraint.type in (constraint.TYPES.COUPLING, constraint.TYPES.RIGID_BODY):
            out_str += write_coupling(constraint)
        else:
            raise NotImplementedError(f'Constraint type "{constraint.type}" is not yet supported')

    return out_str


def _slave_nodes(members):
    """Unique slave nodes of a coupling/rigid-body set. The set may hold nodes directly or
    elements (e.g. a rigid body whose region is an element set) — flatten those to their
    nodes, de-duplicated and order-preserving."""
    nodes = {}
    for m in members:
        for n in getattr(m, "nodes", [m]):  # element -> its nodes; node -> itself
            nodes.setdefault(n.id, n)
    return list(nodes.values())


def write_coupling(constraint: Constraint) -> str:
    out_str = ""
    master = constraint.m_set.members[0]
    master_id = master.id
    for node in _slave_nodes(constraint.s_set.members):
        if node.id == master_id:
            continue  # the reference node can't depend on itself
        slave_id = node.id
        res = LinDep(master.p, node.p)
        lin_deps = [(slave_id, master_id, 3, 9)]
        for lin_dep_rel in res.to_integer_list():
            lin_deps.append(tuple(list(lin_dep_rel) + [0.0]))
        out_str += write_ff("BLDEP", lin_deps)

    return out_str
