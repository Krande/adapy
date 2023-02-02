from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem.exceptions.element_support import IncompatibleElements
from ada.fem.formats.utils import get_fem_model_from_assembly
from ada.fem.loads import LoadGravity
from ada.fem.steps import Step

from .write.write_elements import must_be_converted_to_general_section

if TYPE_CHECKING:
    from ada import Assembly


def check_compatibility(assembly: Assembly):
    p = get_fem_model_from_assembly(assembly)
    step = assembly.fem.steps[0] if len(assembly.fem.steps) > 0 else None

    if step is not None:
        step_types = [x.type for x in assembly.fem.steps]
        if Step.TYPES.EIGEN in step_types:
            for line in p.fem.elements.lines:
                if must_be_converted_to_general_section(line.fem_sec.section.type):
                    raise IncompatibleElements("Calculix does not support general beam elements in Eigenvalue analysis")
        if step.TYPES.STATIC in step_types:
            static_step = assembly.fem.steps[step_types.index(step.TYPES.STATIC)]
            has_gravity = False
            for load in static_step.loads:
                if isinstance(load, LoadGravity):
                    has_gravity = True
            for line in p.fem.elements.lines:
                if must_be_converted_to_general_section(line.fem_sec.section.type) and has_gravity:
                    raise IncompatibleElements("The Calculix general U1 elements does not work with gravity loads")
