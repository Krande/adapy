from __future__ import annotations
from ada.fem.steps import StepImplicitDynamic
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import ada


def step_dynamic_str(step: StepImplicitDynamic, part: ada.Part) -> str:

    return ""
