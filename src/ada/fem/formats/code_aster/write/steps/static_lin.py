from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem import StepImplicitStatic

from ..write_loads import write_load
from .fields import create_field_output_str

if TYPE_CHECKING:
    from ada.api.spatial import Part


def step_static_lin_str(step: StepImplicitStatic, part: Part) -> str:
    from ada.fem.exceptions.model_definition import (
        NoBoundaryConditionsApplied,
        NoLoadsApplied,
    )

    load_str = "\n".join(list(map(write_load, step.loads)))
    if len(step.loads) == 0:
        raise NoLoadsApplied(f"No loads are applied in step '{step}'")
    load = step.loads[0]
    all_boundary_conditions = part.fem.bcs
    assembly = part.get_assembly()
    if assembly != part:
        for bc in part.get_assembly().fem.bcs:
            if bc not in all_boundary_conditions:
                all_boundary_conditions.append(bc)

    if len(all_boundary_conditions) == 0:
        raise NoBoundaryConditionsApplied("No boundary condition is found for the specified model")

    bc_str = ""
    for bc in all_boundary_conditions:
        bc_str += f"_F(CHARGE={bc.name}),"

    has_shells_or_beams = len(part.fem.sections.lines) > 0 or len(part.fem.sections.shells) > 0
    sec_str = "\n    CARA_ELEM=element," if has_shells_or_beams else ""
    # MECA_STATIQUE auto-populates SIEF_ELGA, which carries sub-points on
    # shell/beam elements (DKT, POU_D_E, ...). IMPR_RESU then needs
    # CARA_ELEM in the RESU block to print those fields to MED — without it
    # Code Aster raises MED2_14 the moment it tries to dump SIEF_ELGA.
    resu_cara_str = ", CARA_ELEM=element" if has_shells_or_beams else ""

    field_str = create_field_output_str(step, part)

    return f"""
{load_str}

result = MECA_STATIQUE(
    MODELE=model,
    CHAM_MATER=material,{sec_str}
    EXCIT=({bc_str}_F(CHARGE={load.name}))
)

{field_str}

IMPR_RESU(
    RESU=_F(RESULTAT=result{resu_cara_str}),
    UNITE=80
)

"""
