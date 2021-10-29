from typing import TYPE_CHECKING

from ada.fem.exceptions.element_support import IncompatibleElements
from ada.fem.formats.utils import get_fem_model_from_assembly
from ada.fem.shapes import ElemType

if TYPE_CHECKING:
    from ada import Assembly


def check_compatibility(assembly: "Assembly"):
    p = get_fem_model_from_assembly(assembly)
    step = assembly.fem.steps[0] if len(assembly.fem.steps) > 0 else None

    if step is not None:
        for el in p.fem.elements.lines:
            if el.type == ElemType.LINE_SHAPES.LINE3:
                raise IncompatibleElements("2nd order beam elements are currently not supported in Code Aster")
