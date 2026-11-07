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


def case_shell_pressures(steps, part: Part) -> dict[str, dict[int, dict[int, float]]]:
    """``{result concept: {order number: {element id: p}}}``: the net pressure on each shell element (the sum of every
    pressure of the case on it) in each case of each linear static step, positive along the element's normal from its
    node order -- Sesam's sign, as a SIN's ``BEUSLO`` gives it (``FORCE_COQUE``'s ``PRES`` is the other way round,
    :data:`.write_loads.PRESSURE_SIGN`).

    What the ``.rmed`` reader gives as ``ResultCases.pressures``, through the sidecar: the pressures are input, which
    the ``.rmed`` does not hold. Cases are numbered as the step writes them (:func:`.steps.static_lin.step_cases`):
    case ``k`` is order number ``k``. A step solved nonlinearly (``STAT_NON_LINE``, whose order numbers are its
    increments) is left out, as is a step whose pressures the writer refuses.
    """
    from ada.fem.exceptions.model_definition import UnsupportedLoadType
    from ada.fem.formats import conversion_report
    from ada.fem.formats.carried_loads import general_step_loads

    from .steps.static_lin import step_cases
    from .write_constraints import step_charges
    from .write_loads import STAGE, net_shell_pressures

    cables = any(fs.is_cable for fs in part.fem.sections.lines)
    out: dict[str, dict[int, dict[int, float]]] = {}
    with conversion_report.collect():  # the step writers report what they find; this only reads the cases
        applied = general_step_loads(steps, STAGE)
        for step, result in zip(steps, result_names(steps)):
            if not result or cables or (step.nl_geom is True and len(step.load_cases) == 0):
                continue
            try:
                cases = step_cases(step, applied.get(id(step)) or [], step_charges(part, step).bcs)
                table = {}
                for k, (_, loads) in enumerate(cases, start=1):
                    pressures = [ld for ld in loads if ld.type == ld.TYPES.PRESSURE]
                    if pressures:
                        net = net_shell_pressures(pressures)
                        table[k] = {el_id: -pres for (_, el_id), (_, pres) in sorted(net.items())}
            except UnsupportedLoadType:
                continue  # refused, by name, where the deck is written
            if table:
                out[result] = table
    return out


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
