from __future__ import annotations

from ada.core.utils import bool2text
from ada.fem.steps import Step, StepEigen, StepImplicitStatic


def step_str(step: StepEigen | StepImplicitStatic):
    from .write_loads import load_str
    from .writer import bc_str, interactions_str

    bcstr = "\n".join([bc_str(bc) for bc in step.bcs.values()]) if len(step.bcs) > 0 else "** No BCs"
    lstr = "\n".join([load_str(l) for l in step.loads]) if len(step.loads) > 0 else "** No Loads"

    int_str = (
        "\n".join([interactions_str(interact) for interact in step.interactions.values()])
        if len(step.interactions.values()) > 0
        else "** No Interactions"
    )

    nodal = []
    elem = []
    for fi in step.field_outputs:
        nodal += fi.nodal
        elem += fi.element

    nodal_str = "*node file\n" + ", ".join(nodal) if len(nodal) > 0 else "** No nodal output"
    elem_str = "*el file\n" + ", ".join(elem) if len(elem) > 0 else "** No elem output"

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
{bcstr}
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
