from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem.exceptions.element_support import IncompatibleElements
from ada.fem.formats.utils import get_fem_model_from_assembly
from ada.fem.loads import LoadGravity
from ada.fem.steps import Step

from .write.write_elements import is_u1

if TYPE_CHECKING:
    from ada import Assembly


def check_compatibility(assembly: Assembly):
    """Refuse a U1 beam under gravity: ccx 2.23 stops at "*ERROR in e_c3d_u1: no body forces".

    A general-section (``U1``) beam in a frequency step was refused too. That was stale: measured with ccx 2.23, a 4 m
    U1 cantilever of an IPE300 in 32 elements has its first two frequencies at 6.16414 and 22.4099 Hz against
    Euler-Bernoulli's 6.16561 and 22.4616 (U1's mass matrix carries rotary inertia).
    """
    p = get_fem_model_from_assembly(assembly)
    step = assembly.fem.steps[0] if len(assembly.fem.steps) > 0 else None

    if step is not None:
        step_types = [x.type for x in assembly.fem.steps]
        if step.TYPES.STATIC in step_types:
            static_step = assembly.fem.steps[step_types.index(Step.TYPES.STATIC)]
            has_gravity = False
            for load in static_step.loads:
                if isinstance(load, LoadGravity):
                    has_gravity = True
            for line in p.fem.elements.lines:
                if is_u1(line) and has_gravity:
                    raise IncompatibleElements("The Calculix general U1 elements does not work with gravity loads")
