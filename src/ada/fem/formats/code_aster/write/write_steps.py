from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem import StepEigen, StepImplicitStatic

if TYPE_CHECKING:
    from ada.api.spatial import Part

from .steps import dynamic, eigen, static


def create_step_str(step: StepEigen | StepImplicitStatic, part: Part) -> str:
    st = StepEigen.TYPES
    step_map = {st.STATIC: static.step_static_str, st.EIGEN: eigen.step_eig_str, st.DYNAMIC: dynamic.step_dynamic_str}

    step_writer = step_map.get(step.type, None)

    if step_writer is None:
        raise NotImplementedError(f'Step type "{step.type}" is not yet supported')

    return step_writer(step, part)
