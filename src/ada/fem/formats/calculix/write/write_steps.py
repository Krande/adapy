from __future__ import annotations

from typing import TYPE_CHECKING

from ada.core.utils import bool2text
from ada.fem.steps import Step, StepEigen, StepImplicitStatic

if TYPE_CHECKING:
    from .writer import DeckContext


def has_load_cases(step: Step) -> bool:
    return step.type == Step.TYPES.STATIC and not isinstance(step, StepEigen) and len(step.load_cases) > 0


def steps_str(steps: list[Step], deck: DeckContext) -> str:
    """Every step, in order: a step of load cases as one ``*STEP`` per case, any other step as one ``*STEP``.

    Each static ``*STEP`` opens with ``*CLOAD, OP=NEW`` and ``*DLOAD, OP=NEW`` (measured, ccx 2.23: that removes what
    came before, also as a line of its own followed by a second ``*CLOAD``) and lists the loads it is solved for: a load
    case its own loads, a general step its own and the ones it carries from the general steps before it
    (:func:`ada.fem.formats.carried_loads.general_step_loads`). Left to carry by themselves, a load written as nodal
    ``*CLOAD`` lines would be replaced wherever a later step loads the same node and dof.
    """
    from ada.fem.formats.carried_loads import general_step_loads

    from .write_loads import STAGE

    applied = general_step_loads(steps, STAGE)
    out = []
    for step in steps:
        if has_load_cases(step):
            out += [load_case_step_str(step, lc, deck) for lc in step.load_cases.values()]
            continue
        out.append(step_str(step, deck, loads=applied.get(id(step))))
    return "\n".join(out)


def _loads_str(loads, deck: DeckContext) -> str:
    from .write_loads import load_str

    return "\n".join([load_str(ld, deck) for ld in loads]) if len(loads) > 0 else "** No Loads"


def _outputs_str(step: Step) -> tuple[str, str]:
    nodal = []
    elem = []
    for fi in step.field_outputs:
        nodal += fi.nodal
        elem += fi.element
    nodal_str = "*node file\n" + ", ".join(nodal) if len(nodal) > 0 else "** No nodal output"
    elem_str = "*el file\n" + ", ".join(elem) if len(elem) > 0 else "** No elem output"
    return nodal_str, elem_str


def _bcs_str(step: Step) -> str:
    from .writer import bc_str

    return "\n".join([bc_str(bc) for bc in step.bcs.values()]) if len(step.bcs) > 0 else "** No BCs"


def load_case_step_str(step: StepImplicitStatic, lc, deck: DeckContext) -> str:
    """One load case of a static step as a linear ``*STEP`` of its own, starting from no load."""
    from ada.fem.formats import conversion_report

    from .write_loads import STAGE

    rep = conversion_report.current()
    first = next(iter(step.load_cases.values())) is lc
    if first:
        if step.nl_geom:
            rep.approximated(
                STAGE, "Step", step.name, "a step of load cases is written as linear steps, one per case; nlgeom is not"
            )
        in_cases = {id(ld) for case in step.load_cases.values() for ld in (case.loads or [])}
        for load in step.loads:
            if id(load) not in in_cases:
                rep.omitted(
                    STAGE, "Load", load.name, "a load of a step with load cases, in none of them", step=step.name
                )
    nodal_str, elem_str = _outputs_str(step)
    head = f"""*Step, nlgeom=NO, inc={step.total_incr}
*Static
 {step.init_incr}, {step.total_time}, {step.min_incr}, {step.max_incr}"""
    return f"""**
** STEP: {step.name}  LOAD CASE: {lc.name}
**
{head}
**
** BOUNDARY CONDITIONS
**
{_bcs_str(step)}
**
** LOADS (this load case only)
**
*Cload, OP=NEW
*Dload, OP=NEW
{_loads_str(lc.loads or [], deck)}
**
** OUTPUT REQUESTS
**
{nodal_str}
{elem_str}
*End Step"""


def step_str(step: StepEigen | StepImplicitStatic, deck: DeckContext, loads=None):
    """One ``*STEP``; ``loads``, for a static step, the loads it is solved for, written after ``OP=NEW``."""
    from .writer import interactions_str

    if loads is None:
        lstr = _loads_str(step.loads, deck)
    else:
        lstr = "*Cload, OP=NEW\n*Dload, OP=NEW\n" + _loads_str(loads, deck)

    int_str = (
        "\n".join([interactions_str(interact) for interact in step.interactions.values()])
        if len(step.interactions.values()) > 0
        else "** No Interactions"
    )

    nodal_str, elem_str = _outputs_str(step)

    step_type_map = {
        Step.TYPES.STATIC: static_step,
        Step.TYPES.EIGEN: eigen_step,
    }

    step_str_writer = step_type_map.get(step.type, None)
    if step_str_writer is None:
        raise ValueError(f'Currently unsupported Step Type "{step.type}"')

    step_type_str = step_str_writer(step)
    return f"""**
** STEP: {step.name}
**
{step_type_str}
**
** BOUNDARY CONDITIONS
**
{_bcs_str(step)}
**
** LOADS
**
{lstr}
**
** INTERACTIONS
**
{int_str}
**
** OUTPUT REQUESTS
**
{nodal_str}
{elem_str}
*End Step"""


def static_step(step: StepImplicitStatic):
    return f"""*Step, nlgeom={bool2text(step.nl_geom)}, inc={step.total_incr}
*Static
 {step.init_incr}, {step.total_time}, {step.min_incr}, {step.max_incr}"""


def eigen_step(step: StepEigen):
    return f"""*Step, name={step.name}
*Frequency
 {step.num_eigen_modes}"""
