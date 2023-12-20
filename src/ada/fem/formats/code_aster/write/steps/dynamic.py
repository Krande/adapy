from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem.steps import StepImplicitDynamic

if TYPE_CHECKING:
    import ada


def step_dynamic_str(step: StepImplicitDynamic, part: ada.Part) -> str:
    return ""
