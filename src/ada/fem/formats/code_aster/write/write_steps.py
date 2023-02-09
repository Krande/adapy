from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem import StepEigen, StepImplicit

if TYPE_CHECKING:
    from ada.concepts.spatial import Part

from .steps import eigen, static


def create_step_str(step: StepEigen | StepImplicit, part: Part) -> str:
    st = StepEigen.TYPES
    step_map = {st.STATIC: static.step_static_str, st.EIGEN: eigen.step_eig_str}

    step_writer = step_map.get(step.type, None)

    if step_writer is None:
        raise NotImplementedError(f'Step type "{step.type}" is not yet supported')

    return step_writer(step, part)
