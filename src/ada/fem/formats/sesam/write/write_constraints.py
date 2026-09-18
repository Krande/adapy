import numpy as np

from ada import FEM
from ada.config import logger
from ada.fem import Constraint
from ada.fem.common import LinDep
from ada.fem.surfaces import surface_nodes

from .write_utils import write_ff


def constraint_str(fem: FEM) -> str:
    out_str = ""
    for constraint in fem.constraints.values():
        # A rigid body links every slave node rigidly to the master (reference) node — the
        # same kinematic relation Sesam expresses with BLDEP linear-dependency cards, so it
        # writes identically to a coupling.
        if constraint.type in (constraint.TYPES.COUPLING, constraint.TYPES.RIGID_BODY):
            out_str += write_coupling(constraint)
        elif constraint.type == constraint.TYPES.SHELL2SOLID:
            out_str += write_shell2solid(constraint)
        else:
            raise NotImplementedError(f'Constraint type "{constraint.type}" is not yet supported')

    return out_str


def _bldep(master, slave) -> str:
    """One BLDEP record tying a slave node rigidly to a master node.

    ``SLAVE MASTER NDDOF NDEP`` then NDEP ``(s_i, m_i, beta_i)`` triplets, beta_i
    being master dof m_i's contribution to slave dof s_i. The slave's three
    translations follow the master's translation plus its rotation about the lever
    arm between them: 3 dependent dofs, 9 triplets.
    """
    lin_deps = [(slave.id, master.id, 3, 9)]
    for lin_dep_rel in LinDep(master.p, slave.p).to_integer_list():
        lin_deps.append(tuple(list(lin_dep_rel) + [0.0]))
    return write_ff("BLDEP", lin_deps)


def write_coupling(constraint: Constraint) -> str:
    """A coupling / rigid body as BLDEP links from every slave node to the master.

    Both sides go through ``surface_nodes``: either may be given as a ``Surface``
    rather than a ``FemSet`` (Abaqus writes ``*Coupling`` with ``surface=``), and a
    set may hold elements rather than nodes, as a rigid body over an element region
    does.
    """
    masters = surface_nodes(constraint.m_set)
    if not masters:
        logger.warning(
            "sesam writer: coupling %s has no master node and is written as nothing.",
            constraint.name,
        )
        return ""
    master = masters[0]

    out_str = []
    for node in surface_nodes(constraint.s_set):
        if node.id == master.id:
            continue  # the reference node can't depend on itself
        out_str.append(_bldep(master, node))

    return "".join(out_str)


def write_shell2solid(constraint: Constraint) -> str:
    """Shell-to-solid coupling as BLDEP linear dependencies.

    Each solid-face node depends on the nearest shell-edge node through the rigid link
    :func:`_bldep` writes, which is what gives the solid nodes -- carrying no rotational
    dofs of their own -- the shell edge's bending.

    This is a rigid transition: stiffer than the distributing constraint Abaqus smears
    over an influence region. The two agree on rigid-body motion, not on how load
    spreads into the solid at the interface.
    """
    masters = surface_nodes(constraint.m_set)  # shell edge
    slaves = surface_nodes(constraint.s_set)  # solid face
    if not masters or not slaves:
        logger.warning(
            "sesam writer: shell-to-solid coupling %s has an empty side (%d shell / %d solid nodes) "
            "and is written as nothing.",
            constraint.name,
            len(masters),
            len(slaves),
        )
        return ""

    nearest = _nearest_master(np.array([n.p for n in slaves]), np.array([n.p for n in masters]))

    out_str = []
    for slave, m_idx in zip(slaves, nearest):
        master = masters[m_idx]
        if slave.id == master.id:
            continue  # a node shared by both surfaces can't depend on itself
        out_str.append(_bldep(master, slave))

    return "".join(out_str)


def _nearest_master(slave_p: np.ndarray, master_p: np.ndarray) -> np.ndarray:
    """Index of the closest master point for each slave point. Chunked so the pairwise
    block stays bounded on interfaces with many nodes."""
    out = np.empty(slave_p.shape[0], dtype=np.int64)
    chunk = max(1, 2_000_000 // max(1, master_p.shape[0]))
    for i in range(0, slave_p.shape[0], chunk):
        block = slave_p[i : i + chunk]
        out[i : i + chunk] = ((block[:, None, :] - master_p[None, :, :]) ** 2).sum(-1).argmin(axis=1)
    return out
