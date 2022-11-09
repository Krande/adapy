from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem import StepImplicit

from ..write_loads import write_load

if TYPE_CHECKING:
    from ada.concepts.spatial import Part


def step_static_lin_str(step: StepImplicit, part: Part) -> str:
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

    return f"""
{load_str}

result = MECA_STATIQUE(
    MODELE=model,
    CHAM_MATER=material,
    CARA_ELEM=element,
    EXCIT=({bc_str}_F(CHARGE={load.name}))
)

result = CALC_CHAMP(
    reuse=result,
    RESULTAT=result,
    CONTRAINTE=("SIGM_ELGA", "SIGM_ELNO"),
    CRITERES=("SIEQ_ELGA", "SIEQ_ELNO"),
)

IMPR_RESU(
    RESU=_F(RESULTAT=result),
    UNITE=80
)

"""
