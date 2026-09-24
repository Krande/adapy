from __future__ import annotations

from itertools import groupby
from typing import Dict, List

from ada.api.nodes import Node
from ada.config import logger
from ada.fem import FEM, Bc, Constraint, FemSet
from ada.fem.formats.utils import str_to_int

from ..write.write_bcs import ALL_DOFS, FREE, RETAINED, SUPERNODE_SET_NAME
from . import cards


def get_constraints(bulk_str, fem: FEM) -> Dict[str, Constraint]:
    """The BLDEP records -> the equations (see :func:`equations_from_bldep`) and one coupling
    per independent (master) node of the rigid links.

    A coupling is back on the deck's own node sets where it can be: the master on a node set
    holding just that node, the dependent nodes on the set whose TDSETNAM names the coupling
    (``write_sets.CONSTRAINT_TAG``, what adapy's writer puts on it), else on a set holding
    exactly them. Only what no set matches gets a generated ``co<master>_m`` / ``_s`` set.
    The coupling takes its name from that comment too, else ``co<master>``. ``fem.sets``
    must already hold the deck's sets.
    """
    con_map = [m.groupdict() for m in cards.re_bldep.finditer(bulk_str)]
    tagged = _constraint_sets(bulk_str, fem)
    rigid = [d for d in con_map if _is_rigid_link(d, fem)]
    constraints: Dict[str, Constraint] = {}
    for c in equations_from_bldep([d for d in con_map if not _is_rigid_link(d, fem)], fem, tagged, bulk_str):
        constraints[c.name] = c
    # A coupling named on its dependent-node set takes the links into that set first: two
    # couplings on one reference node write their links under the same master, and grouping
    # by master alone merged them into one.
    for name, fs in tagged:
        nodes = _set_nodes(fs)
        own = [d for d in rigid if str_to_int(d["slave"]) in nodes]
        masters = {str_to_int(d["master"]) for d in own}
        if name in constraints or len(masters) != 1 or len(own) != len(nodes - masters):
            continue
        constraints[name] = grab_constraint(masters.pop(), own, fem, [(name, fs)] + tagged)
        rigid = [d for d in rigid if not any(d is o for o in own)]
    rigid.sort(key=lambda x: str_to_int(x["master"]))
    for m, d in groupby(rigid, key=lambda x: str_to_int(x["master"])):
        c = grab_constraint(m, list(d), fem, tagged)
        constraints[c.name] = c
    return constraints


def _constraint_sets(bulk_str, fem: FEM) -> list[tuple[str, FemSet]]:
    """``(constraint name, set)`` for every TDSETNAM carrying a constraint comment."""
    from ..write.write_sets import CONSTRAINT_TAG
    from .read_sets import text_record

    out = []
    for m in cards.re_setnames.finditer(bulk_str):
        set_name, comments = text_record(m.groupdict(), "set_name")
        names = [c[len(CONSTRAINT_TAG) :].strip() for c in comments if c.startswith(CONSTRAINT_TAG)]
        if not names:
            continue
        out += [(name, fs) for name in names for fs in fem.sets.sets if fs.name == set_name]
    return out


def _set_nodes(fs: FemSet) -> set[int]:
    if fs.type == "nset":
        return {n.id for n in fs.members}
    return {n.id for el in fs.members for n in el.nodes}


def _dependent_dofs(d: dict) -> list[int]:
    """The s(i) of a BLDEP record's NDEP triplets."""
    return [s for s, _, _ in _bldep_terms(d)]


def grab_constraint(master: int, data: list[dict], fem: FEM, tagged: list[tuple[str, FemSet]] = ()) -> Constraint:
    m_node = fem.nodes.from_id(master)
    slaves = [fem.nodes.from_id(str_to_int(d["slave"])) for d in data]
    slave_ids = {n.id for n in slaves} | {master}
    dofs = sorted({s for d in data for s in _dependent_dofs(d)})

    name, s_set = f"co{master}", None
    for con_name, fs in tagged:
        # The set may hold the master too: a coupling's reference node can't depend on itself,
        # so the writer leaves it out of BLDEP (write_constraints.coupling_records).
        if _set_nodes(fs) | {master} == slave_ids:
            name, s_set = con_name, fs
            break
    nsets = [fs for fs in fem.sets.sets if fs.type == "nset"]
    if s_set is None:
        s_set = next((fs for fs in nsets if _set_nodes(fs) == {n.id for n in slaves}), None)
    if s_set is None:
        s_set = fem.add_set(FemSet(f"co{master}_s", slaves, "nset"))
    # The reference-node set the writer named the coupling on, else the first holding just it.
    own = [fs for n, fs in tagged if n == name and fs is not s_set and fs.type == "nset"]
    m_set = next((fs for fs in own + nsets if _set_nodes(fs) == {master}), None)
    if m_set is None:
        m_set = fem.add_set(FemSet(f"co{master}_m", [m_node], "nset"))

    # BLDEP writes a rigid body over an element region exactly as a coupling; the region being
    # elements is what tells them apart (write_constraints.bldep_records).
    con_type = Constraint.TYPES.RIGID_BODY if s_set.type == "elset" else Constraint.TYPES.COUPLING
    return Constraint(name, con_type, m_set, s_set, dofs=dofs or None, parent=fem)


def _bldep_terms(d: dict) -> list[tuple[int, int, float]]:
    """A BLDEP record's ``(slave dof, master dof, beta)`` triplets, each padded to a line of
    four (a "Not Used" field, manual 8.2.1) or not."""
    vals = [float(x) for x in d["bulk"].split()]
    n = str_to_int(d["ndep"])
    k = 4 if len(vals) >= 4 * n else 3
    return [(int(vals[k * i]), int(vals[k * i + 1]), vals[k * i + 2]) for i in range(n)]


def _is_rigid_link(d: dict, fem: FEM) -> bool:
    """Whether a BLDEP record is the rigid arm a coupling writes (``write_constraints._bldep``):
    the slave's three translations following the master's translation plus its rotation
    about the lever arm between them. Anything else is a general linear dependency, which is
    what an ``*Equation`` is."""
    from ada.fem.common import LinDep

    slave = fem.nodes.from_id(str_to_int(d["slave"]))
    master = fem.nodes.from_id(str_to_int(d["master"]))
    got = {(s, m): b for s, m, b in _bldep_terms(d)}
    want = {(s, m): b for s, m, b in LinDep(master.p, slave.p).to_integer_list()}
    if got.keys() != want.keys():
        return False
    # GCOORD holds nine significant digits, so the lever arm read back is the written one
    # to about that; the betas are compared no closer.
    scale = max(1.0, *(abs(b) for b in want.values()))
    return all(abs(got[k] - want[k]) <= 1e-7 * scale for k in want)


def equations_from_bldep(
    records: list[dict], fem: FEM, tagged: list[tuple[str, FemSet]] = (), bulk_str=""
) -> list[Constraint]:
    """General BLDEP records back as ``*Equation`` constraints.

    ``u(s, d) = sum_i beta_i u(m_i, d_i)`` is the equation ``1 u(s, d) - sum_i beta_i u(m_i,
    d_i) = 0`` with the dependent term first, which is the term Abaqus eliminates. The terms
    of one dependent dof are gathered over every record naming it, in file order.

    An equation named on its eliminated node's TDNODE (``write_sets.EQUATION_TAG``) comes back
    as that equation: the dependent dofs carrying one name are one equation over node sets,
    a term being the set named for it (``write_sets.CONSTRAINT_TAG``) whose nodes, in order,
    are that term's nodes. Its operands are what an Abaqus-read equation's are: the first
    two terms, a node term as the one-node set ``<name>_s`` / ``<name>_m``. Anything else
    is one equation per dependent dof on node terms, named ``eq<node>_<dof>``.
    """
    eqs: dict[tuple[int, int], list] = {}
    for d in records:
        slave = fem.nodes.from_id(str_to_int(d["slave"]))
        master = fem.nodes.from_id(str_to_int(d["master"]))
        for s_dof, m_dof, beta in _bldep_terms(d):
            terms = eqs.setdefault((slave.id, s_dof), [(slave, s_dof, 1.0)])
            terms.append((master, m_dof, -beta))

    by_name: dict[str, list[tuple[int, int]]] = {}
    for key, name in _equation_names(bulk_str).items():
        if key in eqs:
            by_name.setdefault(name, []).append(key)
    out = []
    done: set = set()
    for name, keys in by_name.items():
        con = _named_equation(name, [eqs[k] for k in keys], [fs for n, fs in tagged if n == name], fem)
        if con is not None:
            out.append(con)
            done.update(keys)
    for (sid, s_dof), terms in eqs.items():
        if (sid, s_dof) in done:
            continue
        s_set = FemSet(f"eq{sid}_{s_dof}_s", [terms[0][0]], "nset")
        # The operands an Abaqus-read equation carries: the eliminated node and the next one.
        m_set = FemSet(f"eq{sid}_{s_dof}_m", [terms[1][0]], "nset")
        out.append(
            Constraint(f"eq{sid}_{s_dof}", Constraint.TYPES.EQUATION, m_set, s_set, equation_terms=terms, parent=fem)
        )
    return out


def _equation_names(bulk_str: str) -> dict[tuple[int, int], str]:
    """``{(node, dof): equation name}`` from the TDNODE comments the writer leaves."""
    from ..write.write_sets import EQUATION_TAG
    from .read_sets import text_record

    out = {}
    for m in cards.re_tdnode.finditer(bulk_str):
        d = m.groupdict()
        _, comments = text_record(d, "text")
        for c in comments:
            if c.startswith(EQUATION_TAG):
                dof, _, name = c[len(EQUATION_TAG) :].strip().partition(" ")
                out[(str_to_int(d["nodeno"]), int(dof))] = name.strip()
    return out


def _named_equation(name: str, groups: list[list], sets: list[FemSet], fem: FEM) -> Constraint | None:
    """One equation from the dependent-dof ``groups`` it was written as (one per node of its
    set terms), or ``None`` when they do not line up as one."""
    shape = [(dof, coef) for _, dof, coef in groups[0]]
    if any([(dof, coef) for _, dof, coef in g] != shape for g in groups):
        return None
    terms = []
    for k, (dof, coef) in enumerate(shape):
        column = [g[k][0].id for g in groups]
        fs = next((s for s in sets if s.type == "nset" and [n.id for n in s.members] == column), None)
        if fs is not None:
            terms.append((fs, dof, coef))
        elif len(set(column)) == 1:
            terms.append((groups[0][k][0], dof, coef))
        else:
            return None

    def operand(ref, suffix: str) -> FemSet:
        return ref if isinstance(ref, FemSet) else FemSet(f"{name}_{suffix}", [ref], "nset", parent=fem)

    s_set = operand(terms[0][0], "s")
    m_set = operand(terms[1][0], "m") if len(terms) > 1 else s_set
    return Constraint(name, Constraint.TYPES.EQUATION, m_set, s_set, equation_terms=terms, parent=fem)


def get_bcs(bulk_str, fem: FEM) -> List[Bc]:
    """The BNBCD block -> ada ``Bc`` objects, plus the retained (supernode) node set.

    FIX code 4 is not a boundary condition — it declares a DOF part of the superelement's
    external interface, the thing Presel matches against the assembly — so it does not come
    back as a ``Bc``. It comes back as a node set named
    :data:`~ada.fem.formats.sesam.write.write_bcs.SUPERNODE_SET_NAME`, which is exactly the
    name the writer's convention picks up: a Sesam -> ada -> Sesam round trip keeps the
    interface with no caller action. See :func:`add_supernode_set`.

    BNBCD is per node, and a ``Bc`` is on a node set, so the nodes are grouped back: see
    :func:`group_bcs`. ``fem.sets`` must already hold the deck's sets.
    """
    retained: dict[int, tuple[int, ...]] = {}
    fixed = [nd for nd in (grab_bc(m, fem, retained) for m in cards.re_bnbcd.finditer(bulk_str)) if nd is not None]
    bcs = group_bcs(fem, fixed)
    add_supernode_set(fem, retained)
    return bcs


def group_bcs(fem: FEM, fixed: list[tuple[Node, tuple[int, ...]]]) -> List[Bc]:
    """``Bc`` objects for ``(node, constrained dofs)``, one per named node set where the deck has one.

    Every BNBCD record used to become a ``Bc`` of its own on a generated one-node set
    (``bc<id>_set``), so a boundary condition on a named set came back as one per node, on
    sets nobody named. Now the nodes sharing a DOF pattern are covered by the deck's own node
    sets: one set holding exactly those nodes if there is one, else the largest sets whose
    members all carry that pattern and no node already covered (ties in file order). Only
    the nodes no named set covers fall back to a ``Bc`` each, as before.
    """
    by_pattern: dict[tuple[int, ...], list[Node]] = {}
    for node, dofs in fixed:
        by_pattern.setdefault(dofs, []).append(node)
    pattern_of = {node.id: dofs for node, dofs in fixed}

    nsets = [fs for fs in fem.sets.sets if fs.type == "nset" and len(fs.members) > 0]
    bcs = []
    # Sesam has no name for a boundary condition, so it takes its set's name.
    taken = {b.name for b in fem.bcs}
    for dofs, nodes in by_pattern.items():
        ids = {n.id for n in nodes}
        candidates = [fs for fs in nsets if all(pattern_of.get(m.id) == dofs for m in fs.members)]
        exact = [fs for fs in candidates if {m.id for m in fs.members} == ids]
        chosen = exact[:1]
        if not chosen:
            covered: set[int] = set()
            for fs in sorted(candidates, key=lambda x: -len(x.members)):
                members = {m.id for m in fs.members}
                if members & covered:
                    continue
                chosen.append(fs)
                covered |= members
        for fs in chosen:
            name = fs.name if fs.name not in taken else f"{fs.name}_bc{len(taken)}"
            taken.add(name)
            bc = Bc(name, fs, list(dofs), parent=fem)
            for m in fs.members:
                m.bc = bc
            bcs.append(bc)
        covered = {m.id for fs in chosen for m in fs.members}
        for node in nodes:
            if node.id in covered:
                continue
            fem_set = fem.sets.add(FemSet(f"bc{node.id}_set", [node], "nset"))
            bc = Bc(f"bc{node.id}", fem_set, list(dofs), parent=fem)
            node.bc = bc
            bcs.append(bc)
    return bcs


def grab_bc(match, fem: FEM, retained: dict[int, tuple[int, ...]] | None = None) -> tuple[Node, tuple[int, ...]] | None:
    """One BNBCD record -> ``(node, constrained dofs)``, or ``None`` when the record declares
    no constraint.

    ``retained``, when given, collects ``{node id: (dof, ...)}`` for the DOFs carrying FIX
    code 4. They are gathered before the constraint check below so that a constraint-attached
    node does not lose the flag: retained and dependent are orthogonal statements about
    different DOFs of the same node, even though this writer never emits both.

    ``None`` is returned for a node attached to a constraint (as before — BLDEP owns it),
    and now also for a node whose record holds nothing but codes 0 (free) and 4 (retained).
    Such a node has no boundary condition at all, and the empty ``Bc`` + one-node
    ``bc<id>_set`` it used to get were pure noise: this project's deck has 58 supernodes,
    a large model would have thousands. The set is therefore created only after that
    decision, never before it.
    """
    d = match.groupdict()
    node = fem.nodes.from_id(str_to_int(d["nodeno"]))

    dofs = []
    retained_dofs = []
    for i, c in enumerate(d["content"].replace("\n", "").split()):
        bc_sestype = str_to_int(c.strip())
        if bc_sestype == RETAINED:
            retained_dofs.append(i + 1)
            continue
        if bc_sestype == FREE:
            continue
        # Codes 1 (fixed), 2 (prescribed) and 3 (linearly dependent) all land as an
        # ordinary dof of the Bc, unchanged from before.
        dofs.append(i + 1)

    if retained is not None and retained_dofs:
        retained[node.id] = tuple(retained_dofs)

    for constraint in fem.constraints.values():
        if node in constraint.m_set.members:
            return None
        if node in constraint.s_set.members:
            return None

    if not dofs:
        return None

    return node, tuple(dofs)


def add_supernode_set(fem: FEM, retained: dict[int, tuple[int, ...]]) -> FemSet | None:
    """Put every node that carried FIX code 4 into a node set named ``SESAM_SUPERNODES``.

    That name is the writer's convention (``write_bcs.retained_dofs_from_convention``), so
    the set alone closes the round trip: read a deck, write it back, and the same nodes come
    out with code 4 again.

    **Partial patterns.** The convention retains *all six* DOFs of every member, so the set
    cannot express a record like ``4 4 4 0 0 0``. The choice made here is to create the set
    regardless and warn, rather than to skip it or to invent an alternative carrier:

    * Dropping the set — the "only use it when every node is all six" option — loses the
      interface entirely for such a deck, which is the very defect this function fixes. A
      widened DOF pattern still gives a deck Presel can connect; no set at all does not.
    * The writer's only per-DOF channel is ``metadata["sesam_retained_dofs"]``, a *to_fem
      argument*, not model state. The reader cannot fill it, so "fall back to something
      explicit" would mean inventing set names the writer does not know about and that
      therefore need caller action anyway.

    So the widening happens, but loudly rather than silently: a warning names the affected
    nodes and the DOFs they actually carried. The per-DOF detail itself is *not* preserved —
    that is the honest limit of this round trip, and the warning says so.

    ``None`` when nothing was retained. An existing set of that name (a deck written by ada
    carries it as GSETMEMB, and ``get_sets`` runs first) is extended with whatever is
    missing rather than duplicated.
    """
    if not retained:
        return None

    partial = {nid: dofs for nid, dofs in sorted(retained.items()) if dofs != ALL_DOFS}
    if partial:
        shown = list(partial.items())[:10]
        logger.warning(
            "sesam reader: %s of %s nodes with BNBCD FIX code 4 are retained on fewer than all six dofs, "
            "e.g. %s. They are all put in the node set %s, which the writer retains on all six dofs, so "
            "writing this model back out widens those patterns; the per-dof detail is not preserved.",
            len(partial),
            len(retained),
            ", ".join(f"node {nid}: dofs {list(dofs)}" for nid, dofs in shown),
            SUPERNODE_SET_NAME,
        )
    else:
        logger.info(
            "sesam reader: %s nodes carry BNBCD FIX code 4 on all six dofs; they are the "
            "superelement's external interface and come back as the node set %s.",
            len(retained),
            SUPERNODE_SET_NAME,
        )

    nodes = [fem.nodes.from_id(nid) for nid in sorted(retained)]
    try:
        fem_set = fem.sets.get_nset_from_name(SUPERNODE_SET_NAME)
    except ValueError:
        fem_set = fem.sets.add(FemSet(SUPERNODE_SET_NAME, nodes, "nset", parent=fem))
    else:
        existing = {mem.id for mem in fem_set.members}
        missing = [n for n in nodes if n.id not in existing]
        if missing:
            fem_set.add_members(missing)

    return fem_set
