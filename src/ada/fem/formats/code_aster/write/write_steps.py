from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem import StepEigen, StepImplicitStatic

if TYPE_CHECKING:
    from ada.api.spatial import Assembly, Part

from .steps import dynamic, eigen, static


def all_steps(assembly: Assembly) -> list:
    """Every step the deck carries, in the Abaqus writer's order
    (:func:`ada.fem.formats.abaqus.write.write_steps.abaqus_steps`): the assembly's, then each part FEM's -- where
    ``Part.to_fem_obj`` puts the step its concept load cases become. adapy keeps no creation order across the
    assembly and its parts, so a step added to the assembly after a part's comes first, in all three writers. This
    writer used to leave out the part's.
    """
    from ada.fem.formats.abaqus.write.write_steps import abaqus_steps

    return abaqus_steps(assembly)


def result_names(steps) -> list[str]:
    """One result concept per static step: ``result`` for the first (the name the readers know), ``result2``,
    ``result3``, ... for the next. Every step was written into ``result``, so a second step overwrote the first in the
    deck and in the MED file. Eight characters at most: the MED field name is the concept name padded to 8 plus the
    field's (``result__DEPL``)."""
    names, n = [], 0
    for step in steps:
        if step.type == StepEigen.TYPES.STATIC:
            n += 1
            names.append("result" if n == 1 else f"result{n}")
        else:
            names.append("")
    if n > 99:
        raise ValueError("code_aster writer: more than 99 static steps")
    return names


def steps_str(steps, part: Part) -> str:
    """Every step, each static one into its own result (:func:`result_names`).

    A general static step (one without load cases) is solved for its own loads and the ones it carries from the general
    static steps before it, as Abaqus and CalculiX have it (:func:`ada.fem.formats.carried_loads.general_step_loads`):
    each ``MECA_STATIQUE`` starts from no load, so the earlier loads are listed again. A step of load cases solves each
    case on its own.
    """
    from ada.fem.formats.carried_loads import general_step_loads

    from .write_loads import STAGE

    applied = general_step_loads(steps, STAGE)
    return "\n".join(
        create_step_str(step, part, result, applied.get(id(step))) for step, result in zip(steps, result_names(steps))
    )


def create_step_str(
    step: StepEigen | StepImplicitStatic, part: Part, result: str = "result", loads: list | None = None
) -> str:
    """One step; ``loads``, for a general static step, the loads it is solved for (its own and the carried ones)."""
    st = StepEigen.TYPES
    if step.type == st.STATIC:
        return static.step_static_str(step, part, result, loads or [])
    step_map = {st.EIGEN: eigen.step_eig_str, st.DYNAMIC: dynamic.step_dynamic_str}

    step_writer = step_map.get(step.type, None)

    if step_writer is None:
        raise NotImplementedError(f'Step type "{step.type}" is not yet supported')

    return step_writer(step, part)
