from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ada import FEM
from ada.config import logger
from ada.fem import Constraint
from ada.fem.common import LinDep
from ada.fem.surfaces import surface_nodes

from .write_utils import write_ff


@dataclass(frozen=True)
class BldepRecord:
    """One BLDEP record: a slave node depending on a master node through
    ``(slave_dof, master_dof, beta)`` terms.

    Both the BLDEP writer and the BNBCD companion writer (``write_bcs.bnbcd_str``)
    consume these records, so the linear dependencies the deck declares and the FIX
    codes that must accompany them (manual printed 6-27) can never disagree.
    """

    slave: int
    master: int
    terms: tuple[tuple[int, int, float], ...]

    @property
    def slave_dofs(self) -> tuple[int, ...]:
        """The slave's dependent dofs — BNBCD code 3 goes on exactly these."""
        return tuple(sorted({s for s, _, _ in self.terms}))

    @property
    def master_dofs(self) -> tuple[int, ...]:
        """The master dofs this record reads — the ones the master's own BNBCD record
        has to leave free (code 0)."""
        return tuple(sorted({m for _, m, _ in self.terms}))

    def to_str(self) -> str:
        rows = [(self.slave, self.master, len(self.slave_dofs), len(self.terms))]
        rows += [(s, m, beta, 0.0) for s, m, beta in self.terms]
        return write_ff("BLDEP", rows)


def bldep_records(fem: FEM) -> list[BldepRecord]:
    """Every BLDEP record this FEM's constraints produce, in constraint order.

    Split out of :func:`constraint_str` because the BNBCD block is written *before* the
    BLDEP block (GeniE's order, see ``writer.to_fem``) while its FIX codes are derived
    from these records — so they have to be computed before either block is emitted.
    """
    records: list[BldepRecord] = []
    for constraint in fem.constraints.values():
        # A rigid body links every slave node rigidly to the master (reference) node — the
        # same kinematic relation Sesam expresses with BLDEP linear-dependency cards, so it
        # writes identically to a coupling.
        if constraint.type in (constraint.TYPES.COUPLING, constraint.TYPES.RIGID_BODY):
            records += coupling_records(constraint)
        elif constraint.type == constraint.TYPES.SHELL2SOLID:
            records += shell2solid_records(constraint)
        else:
            raise NotImplementedError(f'Constraint type "{constraint.type}" is not yet supported')

    return records


def constraint_str(fem: FEM) -> str:
    return "".join(r.to_str() for r in bldep_records(fem))


def _bldep(master, slave) -> BldepRecord:
    """One BLDEP record tying a slave node rigidly to a master node.

    ``SLAVE MASTER NDDOF NDEP`` then NDEP ``(s_i, m_i, beta_i)`` triplets, beta_i
    being master dof m_i's contribution to slave dof s_i. The slave's three
    translations follow the master's translation plus its rotation about the lever
    arm between them: 3 dependent dofs, 9 triplets.
    """
    terms = tuple((s, m, beta) for s, m, beta in LinDep(master.p, slave.p).to_integer_list())
    return BldepRecord(slave.id, master.id, terms)


def coupling_records(constraint: Constraint) -> list[BldepRecord]:
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
        return []
    master = masters[0]

    records = []
    for node in surface_nodes(constraint.s_set):
        if node.id == master.id:
            continue  # the reference node can't depend on itself
        records.append(_bldep(master, node))

    return records


def write_coupling(constraint: Constraint) -> str:
    return "".join(r.to_str() for r in coupling_records(constraint))


def shell2solid_records(constraint: Constraint) -> list[BldepRecord]:
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
        return []

    nearest = _nearest_master(
        np.array([n.p for n in slaves]),
        np.array([n.p for n in masters]),
        np.array([n.id for n in masters], dtype=np.int64),
    )

    records = []
    for slave, m_idx in zip(slaves, nearest):
        master = masters[m_idx]
        if slave.id == master.id:
            continue  # a node shared by both surfaces can't depend on itself
        records.append(_bldep(master, slave))

    return records


def write_shell2solid(constraint: Constraint) -> str:
    return "".join(r.to_str() for r in shell2solid_records(constraint))


def _nearest_master(slave_p: np.ndarray, master_p: np.ndarray, master_ids: np.ndarray) -> np.ndarray:
    """Index into ``master_p`` of the closest master point for each slave point.

    Ties are broken on the **lowest master node id**, not on position in the master list.
    Which of two equidistant masters a slave is tied to is arbitrary in physical terms --
    on a symmetric interface both give the same kinematics -- but it must not change
    between runs or between callers, and ``argmin`` alone makes it depend on whatever
    order the region resolver happened to hand ``surface_nodes`` back in. Measured on the
    project model: the same master set in two equally valid orders moved 313 of 8 910
    slaves to a different master, every one of them at a distance difference of exactly
    0.0. A deck that reshuffles its BLDEP pairing between two conversions of the same
    model cannot be diffed, so the rule is pinned here.

    The tie-break stays vectorised: the masters are visited in node-id order, so
    ``argmin`` -- which already returns the *first* minimum -- lands on the lowest id of
    any tied group by construction, and the result is mapped back through the sort
    permutation. No per-slave work is added; the pairwise block is still chunked so it
    stays bounded on interfaces with many nodes.
    """
    order = np.argsort(master_ids, kind="stable")
    master_p = master_p[order]

    out = np.empty(slave_p.shape[0], dtype=np.int64)
    chunk = max(1, 2_000_000 // max(1, master_p.shape[0]))
    for i in range(0, slave_p.shape[0], chunk):
        block = slave_p[i : i + chunk]
        out[i : i + chunk] = ((block[:, None, :] - master_p[None, :, :]) ** 2).sum(-1).argmin(axis=1)
    return order[out]
