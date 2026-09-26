from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from ada import FEM
from ada.fem import Constraint
from ada.fem.common import LinDep
from ada.fem.surfaces import surface_nodes

from .not_held import STAGE, report
from .write_utils import write_ff

if TYPE_CHECKING:
    from .writer import NodeDofs


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


def bldep_records(fem: FEM, ndofs: NodeDofs | None = None) -> list[BldepRecord]:
    """Every BLDEP record this FEM's constraints produce, in constraint order.

    Split out of :func:`constraint_str` because the BNBCD block is written *before* the
    BLDEP block (GeniE's order, see ``writer.to_fem``) while its FIX codes are derived
    from these records — so they have to be computed before either block is emitted.

    A constraint BLDEP cannot express is left out and reported, never raised on: a tie or an
    MPC costs the model that one constraint, not the whole deck. ``ndofs`` (the per-node dof
    count, :class:`writer.NodeDofs`) only decides what is reported: whether a coupling that
    leaves slave rotations free drops a dof the node actually has.
    """
    from .writer import ALL_SIX_DOF

    if ndofs is None:
        ndofs = ALL_SIX_DOF
    rep = report()
    # Each record is carried with the name of the constraint that produced it: ``_merged`` has to
    # say which two constraints disagree when they claim one (slave_dof, master_dof).
    owned: list[tuple[str, BldepRecord]] = []
    claimed: dict[tuple[int, int], str] = {}
    for constraint in fem.constraints.values():
        # A rigid body links every slave node rigidly to the master (reference) node — the
        # same kinematic relation Sesam expresses with BLDEP linear-dependency cards, so it
        # writes identically to a coupling.
        if constraint.type in (constraint.TYPES.COUPLING, constraint.TYPES.RIGID_BODY):
            new = coupling_records(constraint)
            _report_coupling(constraint, new, ndofs)
        elif constraint.type == constraint.TYPES.SHELL2SOLID:
            new = shell2solid_records(constraint)
            if new:
                rep.approximated(
                    STAGE,
                    "Constraint",
                    constraint.name,
                    "shell-to-solid coupling written as rigid links to the nearest shell node, "
                    "stiffer than a distributing coupling",
                    n_links=len(new),
                )
        elif constraint.type == constraint.TYPES.EQUATION:
            new = equation_records(constraint)
        else:
            rep.omitted(STAGE, "Constraint", constraint.name, f'a "{constraint.type}" constraint has no Sesam form')
            continue
        owned += [(constraint.name, r) for r in new]
        for key in sorted({(r.slave, dof) for r in new for dof in r.slave_dofs}):
            first = claimed.setdefault(key, constraint.name)
            if first != constraint.name:
                rep.suspect(
                    STAGE,
                    "Constraint",
                    constraint.name,
                    "makes a dof dependent that another constraint already does; nothing in the "
                    "deck says which of the two relations is meant",
                    node=key[0],
                    dof=key[1],
                    other=first,
                )

    return _merged(owned)


#: How close two betas have to be to count as the same dependency declared twice rather than two
#: different ones. Duplicates come from the same arithmetic (the same ``*Equation`` read twice,
#: the same lever arm through ``LinDep``) and agree bit for bit; the window is here only so that
#: float noise is not read as a deliberately different coefficient.
_BETA_SAME = 1e-12


def _merged(owned: list[tuple[str, BldepRecord]]) -> list[BldepRecord]:
    """One record per (slave, master) pair, and one term per (slave_dof, master_dof), in
    first-seen order. ``owned`` pairs each record with the name of the constraint it came from.

    The manual (BLDEP, section 7.2.14): "The same combination of SLAVE and MASTER may occur
    only once." Two equations relating the same pair of nodes -- or an equation with two
    terms on one independent node -- therefore share one record holding all their terms.

    A ``(slave_dof, master_dof)`` may not repeat *inside* that record either, and used to: the
    terms were concatenated, and Sestra adds the betas of a repeated pair together. A deck
    declaring ``u(1,1) = 1.0 * u(2,1)`` twice came out as ``NDDOF 1 NDEP 2`` with beta 1.0
    twice, which is ``u(1,1) = 2.0 * u(2,1)`` -- the dependency doubled, with nothing in the
    written deck to show it had been meant once. What produced the repeat decides what to do
    with it:

    * the same beta twice is one dependency declared twice (the same ``*Equation`` pasted in
      again, two Abaqus keywords the reader turned into the same relation). Written once, and
      noted -- nothing is lost.
    * two different betas are two incompatible statements about one dof: a ``*Tie`` and an
      ``*Equation`` over the same node pair, which is a modelling error in the source deck.
      Summing them is not what either constraint says and neither is averaging, so the later
      one is refused by name in the report and the first-declared relation is what is written.
    """
    rep = report()
    merged: dict[tuple[int, int], dict[tuple[int, int], float]] = {}
    owners: dict[tuple[int, int, int, int], str] = {}
    for name, r in owned:
        terms = merged.setdefault((r.slave, r.master), {})
        for s_dof, m_dof, beta in r.terms:
            first = owners.setdefault((r.slave, r.master, s_dof, m_dof), name)
            if (s_dof, m_dof) not in terms:
                terms[(s_dof, m_dof)] = beta
                continue
            kept = terms[(s_dof, m_dof)]
            if abs(beta - kept) <= _BETA_SAME * max(1.0, abs(beta), abs(kept)):
                rep.note(
                    STAGE,
                    "Constraint",
                    name,
                    "declares a linear dependency another constraint already declares; it is "
                    "written once, as Sestra would otherwise add the two together",
                    node=r.slave,
                    master=r.master,
                    dof=s_dof,
                    beta=beta,
                    other=first,
                )
            else:
                rep.omitted(
                    STAGE,
                    "Constraint",
                    name,
                    "gives a dof a different dependency on the same master dof than another "
                    "constraint already does; BLDEP holds one, so the first one is kept",
                    node=r.slave,
                    master=r.master,
                    dof=s_dof,
                    beta=beta,
                    kept=kept,
                    other=first,
                )
    return [BldepRecord(s, m, tuple((sd, md, b) for (sd, md), b in terms.items())) for (s, m), terms in merged.items()]


def _report_coupling(constraint: Constraint, records: list[BldepRecord], ndofs: NodeDofs) -> None:
    """Say where the rigid links :func:`coupling_records` writes differ from the coupling.

    What is written is the rigid-body motion of each slave's *translations* (dofs 1-3). A
    coupling that also holds slave rotations -- a rigid body always does, a kinematic
    coupling when it names dofs 4-6 -- leaves them free here; one naming fewer than all three
    translations gets all three. An undeclared dof list on a hand-built coupling is its
    constructor default, not a statement, and is taken to mean the translations.
    """
    if not records:
        return
    from ada.fem.constraints import expand_dofs

    rep = report()
    if constraint.type == constraint.TYPES.RIGID_BODY:
        declared = set(range(1, 7))
    elif constraint.dofs_declared:
        declared = set(expand_dofs(constraint.dofs))
    else:
        return

    rot = sorted(declared & {4, 5, 6})
    n_free = sum(1 for r in records for dof in rot if dof <= ndofs.ndof(r.slave))
    if n_free:
        rep.approximated(
            STAGE,
            "Constraint",
            constraint.name,
            "slave rotations are left free; BLDEP is written for the three translations only",
            dofs=rot,
            n_slaves=len(records),
        )
    missing = sorted({1, 2, 3} - declared)
    if missing:
        rep.approximated(
            STAGE,
            "Constraint",
            constraint.name,
            "all three slave translations are made dependent, not only the declared ones",
            undeclared_dofs=missing,
        )


def equation_records(constraint: Constraint) -> list[BldepRecord]:
    """An ``*Equation`` as BLDEP linear dependencies, exactly.

    ``sum_i A_i u(n_i, d_i) = 0`` with the first term the eliminated one is
    ``u(n_1, d_1) = sum_{i>1} (-A_i / A_1) u(n_i, d_i)`` -- one dependent dof depending on
    the others with factor ``beta_i = -A_i / A_1``, which is what BLDEP's ``(s_i, m_i,
    beta_i)`` triplets say, one record per independent node (manual, section 7.2.14).

    A term naming a node set stands for each of its nodes in turn, as in Abaqus: the i-th
    equation takes the i-th node of every set, so the sets must be equally long. An
    equation that relates a node to itself has no BLDEP form (SLAVE and MASTER are two
    nodes); it is left out and reported, as is one with unequal set lengths.
    """
    from ada.fem import FemSet

    rep = report()
    terms = constraint.equation_terms or ()
    if len(terms) < 2:
        rep.omitted(STAGE, "Constraint", constraint.name, "an equation of fewer than two terms has no BLDEP form")
        return []

    columns = [list(ref.members) if isinstance(ref, FemSet) else [ref] for ref, _, _ in terms]
    lengths = {len(c) for c in columns if len(c) != 1}
    if len(lengths) > 1:
        rep.omitted(STAGE, "Constraint", constraint.name, "the node sets of an equation differ in length")
        return []
    n_eq = lengths.pop() if lengths else 1

    records = []
    for i in range(n_eq):
        nodes = [c[i] if len(c) > 1 else c[0] for c in columns]
        slave, s_dof, s_coef = nodes[0], int(terms[0][1]), float(terms[0][2])
        by_master: dict[int, dict[int, float]] = {}
        for node, (_, dof, coef) in zip(nodes[1:], terms[1:]):
            if node.id == slave.id:
                rep.omitted(
                    STAGE,
                    "Constraint",
                    constraint.name,
                    "an equation relating two dofs of one node has no BLDEP form",
                    node=slave.id,
                )
                return []
            per = by_master.setdefault(node.id, {})
            per[int(dof)] = per.get(int(dof), 0.0) - float(coef) / s_coef
        records += [
            BldepRecord(slave.id, m, tuple((s_dof, m_dof, beta) for m_dof, beta in per.items()))
            for m, per in by_master.items()
        ]
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
        report().omitted(STAGE, "Constraint", constraint.name, "a coupling with no master node")
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
        report().omitted(
            STAGE,
            "Constraint",
            constraint.name,
            "a shell-to-solid coupling with an empty side",
            n_shell=len(masters),
            n_solid=len(slaves),
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
