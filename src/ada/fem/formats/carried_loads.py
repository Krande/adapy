"""The loads a general static step is solved for, for a writer whose steps each start from no load.

adapy's steps follow Abaqus: a load of a general step carries into the next general step (measured with ccx 2.23 as
well: a tip load along z in step 1 and one along y in step 2 left step 2 deflected along both). A Code_Aster
``MECA_STATIQUE`` starts from no load, and so does a CalculiX ``*STEP`` that opens with ``OP=NEW`` -- which the
Calculix writer needs, because it writes distributed loads as nodal ``*CLOAD`` lines and ccx *replaces* a load on a
node and dof that an earlier step loaded (measured: a uniform line load's nodal force at mid-span replaced step 1's
point load there). So both writers list each general step's own loads together with the ones it carries, and they
add up -- as the step's own lines do in one step (ccx 2.23: two ``*CLOAD`` lines on one node and dof, and two
``GRAV`` lines on one set, each doubled the deflection).

Two things Abaqus does differently are reported rather than reproduced: a step that loads a region with the same kind
of load an earlier step put there (Abaqus replaces the earlier load; here the two add), and a general step after a
step of load cases (each case is solved on its own, as an Abaqus ``*Load Case`` of a perturbation step is, and the
loads of the general steps before the cases are not carried past them).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada.fem import Load
    from ada.fem.steps import Step


def _region(load: Load) -> tuple:
    """What a later load must share with an earlier one for Abaqus to replace it: the kind and the region, and for a
    point load the dofs it loads (a *CLOAD replaces per node and dof), for gravity its direction (ccx 2.23: a second
    step's GRAV on the same set replaced the first along the same direction and added to it along another)."""
    from ada.fem.loads import acceleration_vector

    surface = getattr(load, "surface", None)
    target = surface if surface is not None else load.fem_set
    key = (load.type, None if target is None else target.name)
    if load.type == load.TYPES.FORCE:
        forces = load.forces_global or []
        return key + (frozenset(i for i, f in enumerate(forces) if f),)
    if load.type in (load.TYPES.GRAVITY, load.TYPES.ACC):
        acc = acceleration_vector(load)
        norm = sum(a * a for a in acc) ** 0.5 or 1.0
        return key + (tuple(round(a / norm, 12) for a in acc),)
    return key


def _replaces(later: tuple, earlier: tuple) -> bool:
    if later[:2] != earlier[:2]:
        return False
    if len(later) == 3 and isinstance(later[2], frozenset):
        return bool(later[2] & earlier[2])
    return later == earlier


def general_step_loads(steps: list[Step], stage: str) -> dict[int, list[Load]]:
    """``{id(step): loads}`` for every general static step (a static step without load cases): the loads of the
    general static steps before it since the last step of load cases, then its own."""
    from ada.fem.formats import conversion_report

    rep = conversion_report.current()
    out: dict[int, list] = {}
    carried: list = []
    dropped: list = []
    for step in steps:
        if step.type != step.TYPES.STATIC:
            continue
        if len(step.load_cases) > 0:
            dropped, carried = dropped + carried, []
            continue
        if dropped:
            rep.approximated(
                stage,
                "Step",
                step.name,
                "a general step after a step of load cases; the loads of the general steps before those cases do not "
                "carry into this one",
                loads=", ".join(sorted(ld.name for ld in dropped)),
            )
            dropped = []
        regions = [_region(ld) for ld in step.loads]
        for ld in carried:
            if any(_replaces(r, _region(ld)) for r in regions):
                rep.approximated(
                    stage,
                    "Step",
                    step.name,
                    "a load carried from an earlier step on a region this step loads again with the same kind of load; "
                    "Abaqus replaces the earlier load, here the two add",
                    load=ld.name,
                )
        own = [ld for ld in step.loads if all(ld is not c for c in carried)]
        out[id(step)] = carried + own
        carried = carried + own
    return out
