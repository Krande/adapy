"""The prescribed displacements (settlements) of a model, case by case, for a writer that solves each step or load
case from no load: the Calculix and Code_Aster writers.

A settlement is a displacement :class:`~ada.fem.Bc` carrying a nonzero magnitude
(:func:`ada.fem.formats.abaqus.write.write_bc.is_settlement`). One naming a load case in ``metadata[BC_LOAD_CASE]``
belongs to that case (GeniE gives one support a different value in each case, and a combination its cases' values
times their factors: :mod:`ada.fem.concept.to_fem`, :mod:`ada.fem.concept.loads_to_fem`); one naming none belongs to
every static step and case. Every dof any settlement prescribes is given a value in every case a writer solves: the
case's own, or zero -- as the Abaqus writer resets a settlement of another case to zero
(:func:`ada.fem.formats.abaqus.write.write_bc.prescribed_in_step_str`), and as Sestra holds a prescribed dof with no
BNDISPL in a load case at zero.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from ada.fem import Bc

#: A prescribed dof: the node set's name and the dof (1-6).
Key = tuple[str, int]


def settlements(bcs: Iterable[Bc]) -> list[Bc]:
    from ada.fem.formats.abaqus.write.write_bc import is_settlement

    return [bc for bc in bcs if is_settlement(bc)]


def load_case_of(bc: Bc) -> str | None:
    from ada.fem.constraints import BC_LOAD_CASE

    return (bc.metadata or {}).get(BC_LOAD_CASE)


def prescribed_dofs(bcs: Iterable[Bc]) -> dict[Key, Bc]:
    """Every dof a settlement prescribes, in the order first met, and the first settlement prescribing it."""
    out: dict[Key, Bc] = {}
    for bc in settlements(bcs):
        for d in bc.dofs:
            if d is not None:
                out.setdefault((bc.fem_set.name, int(d)), bc)
    return out


def case_values(bcs: Iterable[Bc], cases: set[str], stage: str, **where) -> dict[Key, float]:
    """``{(set, dof): value}`` in a step or load case standing for the load case names ``cases``: every prescribed
    dof, its value from the settlement naming one of ``cases`` (or naming none), else zero.

    Two settlements giving the same dof a value in one case: the first one's is kept and the second reported
    ``omitted``, as the Abaqus writer does. ``where`` (``step=...``, ``load_case=...``) goes into that finding.
    """
    from ada.fem.formats import conversion_report

    bcs = list(bcs)
    values = {key: 0.0 for key in prescribed_dofs(bcs)}
    given: dict[Key, str] = {}
    for bc in settlements(bcs):
        case = load_case_of(bc)
        if case is not None and case not in cases:
            continue
        keys = [(bc.fem_set.name, int(d)) for d in bc.dofs if d is not None]
        clash = sorted({given[k] for k in keys if k in given})
        if clash:
            conversion_report.current().omitted(
                stage,
                "prescribed displacement",
                bc.name,
                "a second prescribed displacement of the same dofs in one step or load case; the first one's values "
                "are written",
                kept=clash,
                **where,
            )
            continue
        for k, m in zip(keys, bc.magnitudes):
            values[k] = 0.0 if m is None else float(m)
            given[k] = bc.name
    return values


def names_a_case(bcs: Iterable[Bc], case: str) -> bool:
    """Whether a settlement names load case ``case``: a case that has one is solved even with no load in it."""
    return any(load_case_of(bc) == case for bc in settlements(bcs))
