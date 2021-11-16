from typing import TYPE_CHECKING

from ada.fem.exceptions.element_support import IncompatibleElements
from ada.fem.formats.utils import get_fem_model_from_assembly
from ada.fem.steps import Step

from .write.write_elements import must_be_converted_to_general_section

if TYPE_CHECKING:
    from ada import Assembly


def check_compatibility(assembly: "Assembly"):

    p = get_fem_model_from_assembly(assembly)
    step = assembly.fem.steps[0] if len(assembly.fem.steps) > 0 else None

    if step is not None:
        if assembly.fem.steps[0].type == Step.TYPES.EIGEN:
            for line in p.fem.elements.lines:
                if must_be_converted_to_general_section(line.fem_sec.section.type):
                    raise IncompatibleElements("Calculix does not support general beam elements in Eigenvalue analysis")
