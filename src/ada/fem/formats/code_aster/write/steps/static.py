from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem import StepImplicitStatic

from .static_lin import step_static_lin_str
from .static_nonlin import step_static_nonlin_str

if TYPE_CHECKING:
    from ada.api.spatial import Part


def step_static_str(step: StepImplicitStatic, part: Part, result: str = "result", applied=()) -> str:
    from ada.fem.formats import conversion_report

    from ..write_loads import STAGE

    if any(fs.is_cable for fs in part.fem.sections.lines):
        # Code_Aster's cable element is nonlinear only (RELATION='CABLE' under STAT_NON_LINE, U3.11.06).
        if len(step.load_cases) > 0:
            from ada.fem.exceptions import IncompatibleElements

            raise IncompatibleElements(
                f"code_aster writer: step {step.name!r} has load cases, which are solved linearly, and the model has "
                "cables, which Code_Aster solves nonlinearly only; put the loads in a general step"
            )
        if step.nl_geom is not True:
            conversion_report.current().note(
                STAGE, "Step", step.name, "a model with cables is solved with STAT_NON_LINE (CABLE is nonlinear only)"
            )
        return step_static_nonlin_str(step, part, result, applied)
    if step.nl_geom is True and len(step.load_cases) > 0:
        conversion_report.current().approximated(
            STAGE, "Step", step.name, "a step of load cases is solved linearly, case by case; nlgeom is not written"
        )
        return step_static_lin_str(step, part, result)
    if step.nl_geom is True:
        return step_static_nonlin_str(step, part, result, applied)
    else:
        return step_static_lin_str(step, part, result, applied)
