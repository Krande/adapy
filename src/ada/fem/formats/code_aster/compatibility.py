from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem.exceptions.element_support import IncompatibleElements
from ada.fem.formats.utils import get_fem_model_from_assembly
from ada.fem.shapes.definitions import ElemType

if TYPE_CHECKING:
    from ada import Assembly


def check_compatibility(assembly: Assembly):
    p = get_fem_model_from_assembly(assembly)
    step = assembly.fem.steps[0] if len(assembly.fem.steps) > 0 else None

    if step is not None:
        has_nonlin = True if True in [x.nl_geom for x in assembly.fem.steps] else False
        for el in p.fem.elements.lines:
            if has_nonlin:
                raise IncompatibleElements(
                    "The standard Euler/Timoshenko beams in Code Aster do not support nonlinear"
                    " material assignment. todo: add option to auto-assign beams linear"
                    "material, and/or add support for fiber beams"
                )
            if el.type == ElemType.LINE_SHAPES.LINE3:
                raise IncompatibleElements("2nd order beam elements are currently not supported in Code Aster")

        for el in p.fem.elements.shell:
            if el.type in (ElemType.SHELL_SHAPES.QUAD8, ElemType.SHELL_SHAPES.TRI6) and has_nonlin:
                raise IncompatibleElements(
                    "The default 2nd order shell elements are currently not supported in "
                    "Code Aster when running the analysis using nonlinear materials"
                )
