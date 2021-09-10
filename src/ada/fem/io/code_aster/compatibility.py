from ada import Assembly
from ada.fem.elements import ElemShapes
from ada.fem.exceptions.element_support import IncompatibleElements
from ada.fem.io.utils import get_fem_model_from_assembly


def check_compatibility(assembly: Assembly):
    p = get_fem_model_from_assembly(assembly)
    step = assembly.fem.steps[0] if len(assembly.fem.steps) > 0 else None

    if step is not None:
        for line in p.fem.elements.lines:
            if line.type in ElemShapes.bm3:
                raise IncompatibleElements("2nd order beam elements are currently not supported in Code Aster")
