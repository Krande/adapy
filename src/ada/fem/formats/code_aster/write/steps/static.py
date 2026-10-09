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

    if step.nl_geom is True and len(step.load_cases) > 0:
        conversion_report.current().approximated(
            STAGE, "Step", step.name, "a step of load cases is solved linearly, case by case; nlgeom is not written"
        )
        return step_static_lin_str(step, part, result)
    if step.nl_geom is True:
        return step_static_nonlin_str(step, part, result, applied)
    else:
        return step_static_lin_str(step, part, result, applied)
