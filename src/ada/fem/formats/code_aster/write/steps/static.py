from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem import StepImplicit

from .static_lin import step_static_lin_str
from .static_nonlin import step_static_nonlin_str

if TYPE_CHECKING:
    from ada.concepts.spatial import Part


def step_static_str(step: StepImplicit, part: Part) -> str:
    if step.nl_geom is True:
        return step_static_nonlin_str(step, part)
    else:
        return step_static_lin_str(step, part)
