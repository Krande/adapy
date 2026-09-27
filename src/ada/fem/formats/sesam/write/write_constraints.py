from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Iterable, Sequence

import numpy as np

from ada import FEM
from ada.fem import Constraint
from ada.fem.common import LinDep
from ada.fem.constraints import ALL_DOFS, expand_dofs
from ada.fem.surfaces import SurfaceFacet, surface_facets, surface_nodes

from .facet_interpolation import FACET_KINDS, WEIGHT_EPS, nearest_facet
from .not_held import STAGE, report
from .write_utils import write_ff

if TYPE_CHECKING:
    from ada.api.nodes import Node

    from .writer import NodeDofs

#: How many node ids a finding names before it stops. The count is always exact; this only
#: decides how many examples an engineer gets without opening the deck.
MAX_IDS_REPORTED = 10


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
    records: list[BldepRecord] = []
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
        elif constraint.type == constraint.TYPES.TIE:
            new = tie_records(constraint, ndofs)
        elif constraint.type == constraint.TYPES.EQUATION:
            new = equation_records(constraint)
        else:
            rep.omitted(STAGE, "Constraint", constraint.name, f'a "{constraint.type}" constraint has no Sesam form')
            continue
        records += new
        for key in {(r.slave, dof) for r in new for dof in r.slave_dofs}:
            first = claimed.setdefault(key, constraint.name)
            if first != constraint.name:
                rep.suspect(
                    STAGE,
                    "Constraint",
                    constraint.name,
                    "makes a dof dependent that another constraint already does; Sesam sums linear dependencies",
                    node=key[0],
                    dof=key[1],
                    other=first,
                )

    return _merged(records)


def _merged(records: list[BldepRecord]) -> list[BldepRecord]:
    """One record per (slave, master) pair, in first-seen order.

    The manual (BLDEP, section 7.2.14): "The same combination of SLAVE and MASTER may occur
    only once." Two equations relating the same pair of nodes -- or an equation with two
    terms on one independent node -- therefore share one record holding all their terms.
    """
    merged: dict[tuple[int, int], list] = {}
    for r in records:
        merged.setdefault((r.slave, r.master), []).extend(r.terms)
    return [BldepRecord(s, m, tuple(terms)) for (s, m), terms in merged.items()]


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
    # A *distributing* coupling spreads the load over the surface by weights; what is written
    # here is a rigid arm per slave node, which is the kinematic form. The two agree on
    # rigid-body motion and on nothing else, so the difference is named rather than left for
    # the engineer to discover in the stress field.
    if constraint.metadata.get("coupling_type") == "distributing":
        rep.approximated(
            STAGE,
            "Constraint",
            constraint.name,
            "a distributing coupling is written as rigid links, which is stiffer: BLDEP cannot "
            "spread the load over the surface by weights",
            n_links=len(records),
        )
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
    return _nearest_master_dist(slave_p, master_p, master_ids)[0]


def _nearest_master_dist(
    slave_p: np.ndarray, master_p: np.ndarray, master_ids: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """``(index into master_p, distance)`` of the closest master point for each slave point.

    The pairing and its tie-break are :func:`_nearest_master`'s, documented there. The distance
    comes back alongside because the caller that needs it -- the tie writer, which has to report
    how far its fallback approximation reached -- would otherwise have to recompute the whole
    pairwise block to get it.
    """
    order = np.argsort(master_ids, kind="stable")
    master_p = master_p[order]

    out = np.empty(slave_p.shape[0], dtype=np.int64)
    d2 = np.empty(slave_p.shape[0], dtype=float)
    chunk = max(1, 2_000_000 // max(1, master_p.shape[0]))
    for i in range(0, slave_p.shape[0], chunk):
        block = slave_p[i : i + chunk]
        sq = ((block[:, None, :] - master_p[None, :, :]) ** 2).sum(-1)
        idx = sq.argmin(axis=1)
        out[i : i + chunk] = idx
        d2[i : i + chunk] = np.take_along_axis(sq, idx[:, None], axis=1)[:, 0]
    return order[out], np.sqrt(d2)


# ── ties ─────────────────────────────────────────────────────────────────────────────────────

#: ``cutoff_rule`` values reported with a tie. The rule is part of the finding because it says
#: which geometry the number came from, and therefore what to look at when it is surprising.
DECLARED_TOL_RULE = "declared position tolerance"
NODE_SPACING_RULE = "max nearest-neighbour node spacing"

#: The position tolerance an interpolated tie uses when the deck declares none, as a fraction
#: of the main surface's largest facet half-diagonal. Abaqus' own undeclared default is
#: likewise a small fraction of the facet size; this is of that order, it is reported with
#: every tie, and declaring ``position tolerance=`` in the deck overrides it outright.
DEFAULT_POS_TOL_FRACTION = 0.05
FACET_FRACTION_RULE = f"{DEFAULT_POS_TOL_FRACTION:.0%} of the largest main-facet half-diagonal"


def tie_records(constraint: Constraint, ndofs: NodeDofs | None = None) -> list[BldepRecord]:
    """An Abaqus ``*Tie`` as BLDEP linear dependencies, interpolated over the main facet.

    A tie used to be left out of the deck altogether -- ``bldep_records`` reported
    ``a "tie" constraint has no Sesam form`` and carried on. It has one. Abaqus ties a
    *secondary* surface to a *main* surface by projecting each secondary node onto the main
    surface and interpolating between the nodes of the **facet** it lands on, and Sesam can say
    exactly that: several BLDEP records may name the same dependent node with different
    independent nodes and Sesam sums them (manual section 7.2.14, *"A node may be dependent on
    many nodes. For each combination of SLAVE and MASTER a new data type ... is given"*). So one
    BLDEP record is written per facet node, each carrying the facet's shape-function weight at
    the projected point times the rigid arm to that node, and the records add up to

    .. code-block:: text

        u_slave   = sum_k w_k [ u_k + theta_k x (x_slave - x_k) ]
        phi_slave = sum_k w_k phi_k                       (when both sides carry rotations)

    over the facet's nodes ``k``. The weights sum to 1 -- that is the property the whole thing
    rests on, because it is what makes any rigid-body motion of the main surface pass through
    exactly. Where the main surface has no rotational dofs (a solid mesh) the relation reduces
    to ``u_slave = sum_k w_k u_k``, which is Abaqus' tie term for term.

    The alternative -- a rigid arm to the nearest main *node* -- is what this module does for a
    shell-to-solid coupling, and it is not where Abaqus ties: on the acceptance deck it is off
    by 16.7 mm on average, of the order of half a plate element. It survives here only as a
    fallback for a main surface with no facets to project onto (a ``NODE``-type surface, a plain
    node set), and :func:`_nearest_node_fallback_reason` names which way it got there.

    **Sides.** ``m_set`` is the main (independent) surface and ``s_set`` the secondary
    (dependent) one -- the same meaning they carry for ``COUPLING`` and ``SHELL2SOLID``. Abaqus'
    own ``*Tie`` data line reads ``secondary, main``, so a reader that maps the first surface to
    ``m_set`` hands this function a tie that is inside out. The reader owns that mapping (see
    ``abaqus.read.reader.get_constraints_from_inp``); this function trusts the model.

    **Which secondary nodes are tied.** The cutoff is what Abaqus' ``position tolerance`` has
    always meant: a distance from the secondary node to the main **surface**, not to the nearest
    node of it, so the declared tolerance and the projection distance are directly comparable.
    See :func:`_projection_cutoff`.

    ``adjust`` does not widen the cutoff. It tells Abaqus to *move* secondary nodes onto the
    main surface; ada never moves nodes, and holding the node rigidly at its own coordinates off
    the facet is exact for rigid-body motion, so not adjusting introduces nothing an engineer
    has to size beyond the reported projection distance. (Treating ``adjust=yes`` as an infinite
    tolerance would tie nodes Abaqus leaves free.) It is reported, not honoured.
    """
    from .writer import ALL_SIX_DOF

    ndofs = ALL_SIX_DOF if ndofs is None else ndofs
    rep = report()

    masters = surface_nodes(constraint.m_set)  # Abaqus main / independent
    slaves = surface_nodes(constraint.s_set)  # Abaqus secondary / dependent
    if not masters or not slaves:
        rep.omitted(
            STAGE,
            "*TIE",
            constraint.name,
            "the tie has an empty surface, so it writes nothing",
            n_main_nodes=len(masters),
            n_secondary_nodes=len(slaves),
        )
        return []

    no_rotation = bool(constraint.metadata.get("no_rotation", False))
    dofs = tuple(d for d in _tie_dofs(constraint) if not (no_rotation and d >= 4))

    all_facets = surface_facets(constraint.m_set)
    facets = [f for f in all_facets if f.shape in FACET_KINDS]
    if facets:
        if len(facets) != len(all_facets):
            # Part of the main surface cannot be projected onto, so a secondary node over it
            # lands on a farther facet or on none at all. Silently narrowing the surface a tie
            # is measured against is the one thing this must not do.
            rep.approximated(
                STAGE,
                "*TIE",
                constraint.name,
                f"tie {constraint.name}: some facets of the main surface have a topology this "
                "writer has no shape functions for, so they are left out of the surface the "
                "secondary nodes are projected onto",
                count=len(all_facets) - len(facets),
                n_main_facets=len(all_facets),
                skipped_facet_shapes=_shape_counts(f for f in all_facets if f.shape not in FACET_KINDS),
            )
        return _interpolated_tie(constraint, facets, masters, slaves, dofs, ndofs, rep)

    why, extra = _nearest_node_fallback_reason(all_facets, constraint)
    return _nearest_node_tie(constraint, masters, slaves, dofs, ndofs, rep, why, extra)


def write_tie(constraint: Constraint, ndofs: NodeDofs | None = None) -> str:
    return "".join(r.to_str() for r in tie_records(constraint, ndofs))


def _tie_dofs(constraint: Constraint) -> tuple[int, ...]:
    """The dofs a tie constrains, as sorted unique ints in 1..6.

    An Abaqus ``*Tie`` ties every dof the two surfaces share unless the block says
    ``no rotation``, so an undeclared specification is all six -- unlike a coupling or a rigid
    body, where an undeclared ``dofs`` is the constructor's default rather than a statement and
    this module's long-standing output is the three translations (see :func:`_report_coupling`).
    An explicitly *empty* specification is also all six: an empty sub-keyword block is how
    Abaqus says "every dof", and reading it as none would make the tie vanish.
    """
    return expand_dofs(constraint.dofs) or ALL_DOFS


def _rigid_terms(
    master: Node,
    slave: Node,
    dofs: Sequence[int],
    master_ndof: int,
    slave_ndof: int,
    weight: float = 1.0,
) -> tuple[tuple[int, int, float], ...]:
    """The BLDEP terms for one dependent node held rigidly to one master, over ``dofs``.

    ``weight`` scales every coefficient. It is 1.0 for the nearest-node tie, where the master
    alone holds the dependent node; it is the facet's shape-function value at the projected
    point for an interpolated tie, where the node hangs off every node of the facet at once and
    Sesam sums the records. The weights of one facet sum to 1, so the summed relation is still a
    rigid-body relation -- that is what makes the interpolation exact rather than a blend of
    approximations. A weight of exactly 1.0 is applied by *not* multiplying, so a record that
    carries no interpolation is bit for bit what :func:`_bldep` writes.

    Three cases, in the order the terms are emitted:

    * **translations (1-3) with a 6-dof master** -- the rigid arm ``LinDep`` writes, filtered to
      the declared rows;
    * **translations with a 3-dof master** -- a solid-mesh node has no rotations, so there is no
      arm to read and the relation degenerates to ``(d, d, weight)``, translation glue. For a
      tie that loses nothing: Abaqus has no rotations there to transfer either, and the summed
      weights give ``u_slave = sum_k w_k u_k``, its tie constraint exactly;
    * **rotations (4-6)** -- ``(d, d, weight)`` identity terms, and only when *both* nodes have
      six dofs. Abaqus itself ignores a rotational dof on a node that has none, so skipping it
      is exact rather than an approximation; writing it would make ``bnbcd_str`` reject this
      writer's own output.
    """
    dofs = tuple(dofs)
    translations = tuple(d for d in dofs if d <= 3)
    rotations = tuple(d for d in dofs if d >= 4)

    terms: list[tuple[int, int, float]] = []
    if translations:
        if master_ndof >= 6:
            arm = [(s, m, beta) for s, m, beta in LinDep(master.p, slave.p).to_integer_list() if s in translations]
        else:
            arm = [(d, d, 1.0) for d in translations]
        terms += arm if weight == 1.0 else [(s, m, beta * weight) for s, m, beta in arm]
    if rotations and master_ndof >= 6 and slave_ndof >= 6:
        terms += [(d, d, weight) for d in rotations]
    return tuple(terms)


def _shape_counts(facets: Iterable[SurfaceFacet]) -> dict[str, int]:
    """``{facet topology: how many}``, for a finding to say what a surface is made of.

    Spelled out rather than counted as bare numbers because "807 facets" and "132 TRI6 faces and
    675 LINE edges" are different pieces of information, and only the second says that a tie's
    main surface is mostly one-dimensional.
    """
    counts: dict[str, int] = {}
    for facet in facets:
        key = str(facet.shape)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _tie_adjust(constraint: Constraint) -> str:
    """``adjust`` as the deck spelled it, lower-cased, ``"no"`` when absent.

    Reported rather than honoured -- see :func:`tie_records` -- so an engineer reading the
    finding can see that the deck asked for node adjustment and that it was not done.
    """
    return str(constraint.metadata.get("adjust", "") or "").strip().lower() or "no"


def _projection_cutoff(constraint: Constraint, facets: Sequence[SurfaceFacet]) -> tuple[float, str]:
    """How far from the main surface a secondary node may be and still be tied, and why.

    ``(cutoff, rule)``, the rule naming which geometry the number came from so that a surprising
    cutoff points at something.

    The declared ``position tolerance`` is used as it stands: it is a distance to the main
    surface, and so is the projection distance, so the two are directly comparable. When the
    deck declares none, Abaqus supplies its own default, which is a small fraction of the main
    surface's facet size; adapy has to supply one too, and :data:`DEFAULT_POS_TOL_FRACTION` of
    the largest facet half-diagonal is it. It is computed from
    :func:`ada.fem.surfaces.surface_facets`, the same reading of the surface the node set comes
    from, and it is reported with every tie so an engineer who disagrees can say
    ``position tolerance=`` in the deck and be obeyed.
    """
    if constraint.pos_tol is not None:
        return float(constraint.pos_tol), DECLARED_TOL_RULE
    return DEFAULT_POS_TOL_FRACTION * _max_facet_half_diagonal(facets), FACET_FRACTION_RULE


def _interpolated_tie(
    constraint: Constraint,
    facets: Sequence[SurfaceFacet],
    masters: list[Node],
    slaves: list[Node],
    dofs: Sequence[int],
    ndofs: NodeDofs,
    rep,
) -> list[BldepRecord]:
    """:func:`tie_records` for a main surface whose facets are known. See there for the form."""
    cutoff, cutoff_rule = _projection_cutoff(constraint, facets)
    # The cutoff is handed to the projection rather than only compared against afterwards: a
    # node with no facet inside the position tolerance is untied whichever facet is nearest, so
    # the projection need not look past it. See ``nearest_facet`` for what that saves.
    match = nearest_facet(np.array([n.p for n in slaves], dtype=float), facets, max_distance=cutoff)

    records: list[BldepRecord] = []
    outside: list[int] = []
    # A node inside the cutoff whose declared dofs it simply does not have. Counted apart from
    # ``outside`` because the two are different problems with different fixes, and calling this
    # one "outside the cutoff" sends the engineer to look at a geometry that is fine.
    no_dofs: list[int] = []
    n_self_pairs = 0
    n_translation_only = 0
    n_rotations_tied = 0
    n_tied = 0
    tied_gap: list[float] = []

    for i, slave in enumerate(slaves):
        gap = float(match.distance[i])
        if gap > cutoff:
            outside.append(slave.id)
            continue
        facet = facets[int(match.facet_index[i])]
        if slave.id in facet.node_ids:
            # The node *is* a node of the main surface: it lies on both surfaces, so the
            # interpolation would name it as its own master with a weight of 1 at the corner it
            # sits on. Exactly the self-dependency the nearest-node form skips.
            n_self_pairs += 1
            continue

        slave_ndof = ndofs.ndof(slave.id)
        new: list[BldepRecord] = []
        translation_only = False
        rotations = False
        for k, master in enumerate(facet.nodes):
            weight = float(match.weights[i, k])
            if abs(weight) < WEIGHT_EPS:
                # A shape function is *exactly* zero at a good many points on its own facet. A
                # record of nine 0.0 coefficients still declares the master's dofs to be read,
                # which bnbcd_str then has to hold free for nothing.
                continue
            master_ndof = ndofs.ndof(master.id)
            terms = _rigid_terms(master, slave, dofs, master_ndof, slave_ndof, weight)
            if not terms:
                continue
            new.append(BldepRecord(slave.id, master.id, terms))
            translation_only = translation_only or master_ndof < 6
            rotations = rotations or any(dof >= 4 for dof, _, _ in terms)

        if not new:
            no_dofs.append(slave.id)
            continue
        records += new
        n_tied += 1
        tied_gap.append(gap)
        n_translation_only += int(translation_only)
        n_rotations_tied += int(rotations)

    if not records:
        # The tie name is in the reason here, and in every other per-tie finding below, for the
        # same reason the measure carries it: findings deduplicate on
        # (kind, stage, keyword, reason) and merge their details first-wins, so a reason shared
        # by two ties would report the *first* tie's cutoff for the second one's nodes.
        rep.omitted(
            STAGE,
            "*TIE",
            constraint.name,
            f"tie {constraint.name}: no secondary node projects onto the main surface within the "
            "position tolerance, or none has the dofs the tie declares, so the tie writes nothing",
            n_secondary_nodes=len(slaves),
            n_main_nodes=len(masters),
            n_main_facets=len(facets),
            n_outside_tolerance=len(outside),
            n_without_declared_dofs=len(no_dofs),
            n_self_pairs=n_self_pairs,
            cutoff=cutoff,
            cutoff_rule=cutoff_rule,
            # Not a minimum projection distance: the projection stops looking past the cutoff, so
            # on this path it has none. The distance to the nearest main *node* is finite, is
            # computed only here, and answers the question an engineer reading this actually has
            # -- how far away the main surface is at all.
            nearest_main_node_min=float(
                _nearest_master_dist(
                    np.array([n.p for n in slaves], dtype=float),
                    np.array([n.p for n in masters], dtype=float),
                    np.array([n.id for n in masters], dtype=np.int64),
                )[1].min()
            ),
        )
        return []

    gaps = np.asarray(tied_gap, dtype=float)
    # A ``note``, not an ``approximated``: a node that projects onto a facet within the tolerance
    # is tied to that facet the way Abaqus ties it, so there is no approximation left to size.
    # What is left to report is *that*, and the numbers that say which tie this is -- above all
    # the projection distance, since a tie whose nodes lie on the surface to within a micron is a
    # different object from one whose nodes are a millimetre off it, and only the deck knows
    # which was meant. The nodes that do not reach a facet are the omission below, which is what
    # ``--strict`` and the report file are for.
    rep.note(
        STAGE,
        "*TIE",
        constraint.name,
        f"tie {constraint.name}: each secondary node is interpolated over the main facet it "
        "projects onto, as one BLDEP record per facet node for Sesam to sum",
        n_secondary_nodes=len(slaves),
        n_main_nodes=len(masters),
        n_main_facets=len(facets),
        main_facet_shapes=_shape_counts(facets),
        n_tied=n_tied,
        n_records=len(records),
        records_per_tied_node=len(records) / n_tied,
        n_outside_tolerance=len(outside),
        n_without_declared_dofs=len(no_dofs),
        n_self_pairs=n_self_pairs,
        n_translation_only=n_translation_only,
        n_rotations_tied=n_rotations_tied,
        dofs_tied=list(dofs),
        pos_tol=constraint.pos_tol,
        cutoff=cutoff,
        cutoff_rule=cutoff_rule,
        adjust=_tie_adjust(constraint),
        proj_max=float(gaps.max()),
        proj_p95=float(np.percentile(gaps, 95)),
        proj_mean=float(gaps.mean()),
    )

    if outside:
        rep.omitted(
            STAGE,
            "*TIE",
            constraint.name,
            f"tie {constraint.name}: a secondary node is farther from the main surface than the "
            "position tolerance allows, so it is left untied",
            count=len(outside),
            cutoff=cutoff,
            cutoff_rule=cutoff_rule,
            first_nodes=outside[:MAX_IDS_REPORTED],
        )

    if no_dofs:
        rep.omitted(
            STAGE,
            "*TIE",
            constraint.name,
            f"tie {constraint.name}: a secondary node inside the position tolerance has none of "
            "the dofs the tie declares, so it is left untied",
            count=len(no_dofs),
            dofs_tied=list(dofs),
            first_nodes=no_dofs[:MAX_IDS_REPORTED],
        )

    _report_bldep_chain(constraint.name, records, rep)
    return records


def _report_bldep_chain(name: str, records: Sequence[BldepRecord], rep) -> None:
    """Report an independent node of these records that is a dependent node of them too.

    A facet interpolation names up to eight independent nodes per dependent node where the
    nearest-node form named one, so the chance that one of them is itself dependent -- a node on
    both surfaces of the same tie, and not at a facet node, so the self-pair check does not catch
    it -- goes up eightfold. Sesam would then have to resolve a dependency whose master is itself
    dependent, which is not something the manual promises; and if it does resolve it, the
    kinematics are not the ones the model declared, because the master's own motion is no longer
    free.

    A ``suspect``: the deck is written, nothing is dropped, and the model may well be exactly
    what the engineer meant -- overlapping tie surfaces are ordinary. But it is invisible in the
    deck, so if the conversion does not raise it nothing will.
    """
    dependents = {r.slave for r in records}
    chained = sorted({r.master for r in records if r.master in dependents})
    if not chained:
        return
    rep.suspect(
        STAGE,
        "BLDEP",
        name,
        f"tie {name}: an independent node of this tie is a dependent node of it as well, so a "
        "linear dependency names a node that is itself linearly dependent",
        count=len(chained),
        first_nodes=chained[:MAX_IDS_REPORTED],
    )


def _nearest_node_fallback_reason(facets: Sequence[SurfaceFacet], constraint: Constraint) -> tuple[str, dict]:
    """Why this tie fell back to the nearest main *node*, as reason tail and finding details.

    Two ways to get there, and they want different things looked at: a main surface that names no
    element facet at all -- a ``NODE``-type surface, or a plain ``FemSet`` of nodes, where there
    is simply no geometry to project onto -- or an element surface every one of whose facets has a
    topology :data:`~ada.fem.formats.sesam.write.facet_interpolation.FACET_KINDS` has no shape
    functions for, which is a gap in this writer and names the shapes so it can be closed.

    The tail is spliced into the fallback finding's reason by :func:`_nearest_node_tie`, which
    owns that finding because it owns the distance distribution that sizes it. It is constant per
    tie -- findings deduplicate on the reason, so it must not vary per node.
    """
    if facets:
        return (
            "no facet of the main surface has a topology this writer can interpolate over, so "
            "each secondary node is tied by a rigid arm to the nearest main node instead of "
            "being interpolated over the facet it projects onto",
            dict(n_main_facets=len(facets), main_facet_shapes=_shape_counts(facets)),
        )
    return (
        "the main surface names no element facet to project onto, so each secondary node is tied "
        "by a rigid arm to the nearest main node instead of being interpolated over the facet it "
        "projects onto",
        dict(main_region=type(constraint.m_set).__name__),
    )


def _nearest_node_tie(
    constraint: Constraint,
    masters: list[Node],
    slaves: list[Node],
    dofs: Sequence[int],
    ndofs: NodeDofs,
    rep,
    why: str,
    extra: dict,
) -> list[BldepRecord]:
    """The tie as a rigid arm to the nearest main *node* -- the fallback, and its measure.

    Reached when the main surface offers no facet to project onto: a ``NODE``-type surface, a
    plain ``FemSet`` of nodes, or (rarer) an element surface every one of whose facets has a
    topology this writer has no shape functions for.

    Its cutoff cannot be a projection distance, because there is nothing to project onto. It is
    ``pos_tol`` plus the largest nearest-neighbour spacing of the main nodes -- the slack between
    a tolerance measured to the surface and a distance measured to a node. That slack is *not* a
    covering radius (see :func:`_main_surface_spacing`) and the resulting cutoff cannot tell an
    in-plane offset from a gap; with no facets in hand there is nothing better, which is the whole
    reason the facet path exists.
    """
    master_p = np.array([n.p for n in masters], dtype=float)
    master_ids = np.array([n.id for n in masters], dtype=np.int64)
    slave_p = np.array([n.p for n in slaves], dtype=float)

    nearest, dist = _nearest_master_dist(slave_p, master_p, master_ids)
    slack = _main_surface_spacing(master_p, master_ids)
    pos_tol = float(constraint.pos_tol) if constraint.pos_tol is not None else 0.0
    cutoff = pos_tol + slack

    records: list[BldepRecord] = []
    outside: list[int] = []
    no_dofs: list[int] = []
    n_self_pairs = 0
    n_translation_only = 0
    n_rotations_tied = 0
    tied_dist: list[float] = []

    for i, slave in enumerate(slaves):
        master = masters[int(nearest[i])]
        if slave.id == master.id:
            n_self_pairs += 1  # a node on both surfaces cannot depend on itself
            continue
        if dist[i] > cutoff:
            outside.append(slave.id)
            continue
        master_ndof = ndofs.ndof(master.id)
        terms = _rigid_terms(master, slave, dofs, master_ndof, ndofs.ndof(slave.id))
        if not terms:
            no_dofs.append(slave.id)
            continue
        records.append(BldepRecord(slave.id, master.id, terms))
        tied_dist.append(float(dist[i]))
        if master_ndof < 6:
            n_translation_only += 1
        if any(dof >= 4 for dof, _, _ in terms):
            n_rotations_tied += 1

    if not records:
        rep.omitted(
            STAGE,
            "*TIE",
            constraint.name,
            f"tie {constraint.name}: no secondary node is close enough to a main-surface node, or "
            "none has the dofs the tie declares, so the tie writes nothing",
            n_secondary_nodes=len(slaves),
            n_main_nodes=len(masters),
            n_outside_cutoff=len(outside),
            n_without_declared_dofs=len(no_dofs),
            n_self_pairs=n_self_pairs,
            cutoff=cutoff,
            cutoff_slack=slack,
            cutoff_rule=NODE_SPACING_RULE,
            dist_min=float(dist.min()),
        )
        return []

    measured = np.asarray(tied_dist, dtype=float)
    # The fallback *is* an approximation, so the finding that names it is the finding that
    # measures it -- an approximation nobody can size is a guess.
    rep.approximated(
        STAGE,
        "*TIE",
        constraint.name,
        f"tie {constraint.name}: {why}",
        n_secondary_nodes=len(slaves),
        n_main_nodes=len(masters),
        n_tied=len(records),
        n_outside_cutoff=len(outside),
        n_without_declared_dofs=len(no_dofs),
        n_self_pairs=n_self_pairs,
        n_translation_only=n_translation_only,
        n_rotations_tied=n_rotations_tied,
        dofs_tied=list(dofs),
        pos_tol=pos_tol,
        cutoff_slack=slack,
        cutoff_rule=NODE_SPACING_RULE,
        cutoff=cutoff,
        adjust=_tie_adjust(constraint),
        dist_max=float(measured.max()),
        dist_p95=float(np.percentile(measured, 95)),
        dist_mean=float(measured.mean()),
        **extra,
    )

    if outside:
        rep.omitted(
            STAGE,
            "*TIE",
            constraint.name,
            f"tie {constraint.name}: a secondary node is farther from the nearest main node than "
            "the position tolerance plus the main surface's own reach allows, so it is left untied",
            count=len(outside),
            cutoff=cutoff,
            cutoff_slack=slack,
            cutoff_rule=NODE_SPACING_RULE,
            first_nodes=outside[:MAX_IDS_REPORTED],
        )

    if no_dofs:
        rep.omitted(
            STAGE,
            "*TIE",
            constraint.name,
            f"tie {constraint.name}: a secondary node inside the cutoff has none of the dofs the "
            "tie declares, so it is left untied",
            count=len(no_dofs),
            dofs_tied=list(dofs),
            first_nodes=no_dofs[:MAX_IDS_REPORTED],
        )

    return records


def _max_facet_half_diagonal(facets: Sequence[SurfaceFacet]) -> float:
    """Half the longest distance between two nodes of one facet, maximised over ``facets``.

    The size of the coarsest facet of the surface, used by :func:`_projection_cutoff` as the
    scale of the default position tolerance. Half the *longest node pair* distance rather than
    the longest edge, because it is the facet's own reach that is wanted and a quadrilateral's
    diagonal is longer than any of its edges.

    ``0.0`` for an empty facet list, which callers do not reach: :func:`tie_records` takes the
    nearest-node path when there are no facets.
    """
    worst_sq = 0.0
    for facet in facets:
        points = facet.points
        if len(points) < 2:
            continue
        sq = ((points[:, None, :] - points[None, :, :]) ** 2).sum(-1)
        worst_sq = max(worst_sq, float(sq.max()))
    return float(np.sqrt(worst_sq)) / 2.0


def _main_surface_spacing(master_p: np.ndarray, master_ids: np.ndarray) -> float:
    """The largest nearest-neighbour distance within the main surface.

    The cutoff slack of :func:`_nearest_node_tie`, the fallback path for a main surface that
    names only nodes and so has no facet to project onto. The *largest* spacing is taken rather
    than the mean so that the coarsest patch of the surface sets it.

    It is **not** a covering radius: the distance from a point on a facet to the nearest node of
    that facet can exceed the nearest-neighbour spacing without limit as the facet is stretched.
    With only the nodes in hand there is nothing better to compute; with the facets in hand there
    is nothing to bound at all, because the projection distance is then measured directly --
    which is why a surface with facets does not come through here.

    ``inf`` for a surface of fewer than two nodes: with a single candidate master there is no
    spacing to measure and no second node to prefer, so nothing should be excluded on distance
    grounds. Same chunked pairwise block as the pairing, with each node's distance to itself
    masked out.
    """
    n = master_p.shape[0]
    if n < 2:
        return float("inf")

    points = master_p[np.argsort(master_ids, kind="stable")]

    worst = 0.0
    chunk = max(1, 2_000_000 // n)
    for i in range(0, n, chunk):
        block = points[i : i + chunk]
        sq = ((block[:, None, :] - points[None, :, :]) ** 2).sum(-1)
        rows = np.arange(block.shape[0])
        sq[rows, rows + i] = np.inf  # a node is not its own neighbour
        worst = max(worst, float(sq.min(axis=1).max()))
    return float(np.sqrt(worst))
