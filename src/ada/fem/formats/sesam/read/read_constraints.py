from __future__ import annotations

from itertools import groupby
from typing import Dict, List, Union

from ada.config import logger
from ada.fem import FEM, Bc, Constraint, FemSet
from ada.fem.formats.utils import str_to_int

from ..write.write_bcs import ALL_DOFS, FREE, RETAINED, SUPERNODE_SET_NAME
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
    """The BNBCD block -> ada ``Bc`` objects, plus the retained (supernode) node set.

    FIX code 4 is not a boundary condition — it declares a DOF part of the superelement's
    external interface, the thing Presel matches against the assembly — so it does not come
    back as a ``Bc``. It comes back as a node set named
    :data:`~ada.fem.formats.sesam.write.write_bcs.SUPERNODE_SET_NAME`, which is exactly the
    name the writer's convention picks up: a Sesam -> ada -> Sesam round trip keeps the
    interface with no caller action. See :func:`add_supernode_set`.
    """
    retained: dict[int, tuple[int, ...]] = {}
    bcs = [bc for bc in (grab_bc(m, fem, retained) for m in cards.re_bnbcd.finditer(bulk_str)) if bc is not None]
    add_supernode_set(fem, retained)
    return bcs


def grab_bc(match, fem: FEM, retained: dict[int, tuple[int, ...]] | None = None) -> Union[Bc, None]:
    """One BNBCD record -> a ``Bc``, or ``None`` when the record declares no constraint.

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

    fem_set = fem.sets.add(FemSet(f"bc{node.id}_set", [node], "nset"))
    bc = Bc(f"bc{node.id}", fem_set, dofs, parent=fem)
    node.bc = bc
    return bc


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
