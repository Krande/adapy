from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem import StepImplicitStatic

from .static_lin import step_static_lin_str
from .static_nonlin import step_static_nonlin_str

if TYPE_CHECKING:
    from ada.api.spatial import Part


def step_static_str(step: StepImplicitStatic, part: Part) -> str:
    if step.nl_geom is True:
        return step_static_nonlin_str(step, part)
    else:
        return step_static_lin_str(step, part)
