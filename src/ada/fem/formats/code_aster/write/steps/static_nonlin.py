from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem import StepImplicit

from ..write_loads import write_load

if TYPE_CHECKING:
    from ada.concepts.spatial import Part


def step_static_nonlin_str(step: StepImplicit, part: Part) -> str:
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

timeReel = DEFI_LIST_REEL(DEBUT=0.0, INTERVALLE=_F(JUSQU_A=1.0, NOMBRE=10))
timeInst = DEFI_LIST_INST(METHODE="AUTO", DEFI_LIST=_F(LIST_INST=timeReel))
bc_step = DEFI_FONCTION(NOM_PARA="INST", VALE=(0.0, 0.0, 1.0, 1.0))

result = STAT_NON_LINE(
    MODELE=model,
    CHAM_MATER=material,
    CARA_ELEM=element,
    # COMPORTEMENT=(_F(DEFORMATION="PETIT", RELATION="VMIS_ISOT_TRAC", TOUT="OUI")),
    COMPORTEMENT=(_F(DEFORMATION="PETIT", TOUT="OUI")),
    CONVERGENCE=_F(ARRET="OUI", ITER_GLOB_MAXI=8,),
    EXCIT=({bc_str}_F(CHARGE={load.name}, FONC_MULT=bc_step)),
    INCREMENT=_F(LIST_INST=timeInst),
    ARCHIVAGE=_F(LIST_INST=timeReel),
)

result = CALC_CHAMP(
    reuse=result, RESULTAT=result,
    CONTRAINTE=("EFGE_ELNO", "EFGE_NOEU", "SIGM_ELNO"),
    DEFORMATION=("EPSI_ELNO", "EPSP_ELNO"),
)

stress = POST_CHAMP(
    EXTR_COQUE=_F(
        NIVE_COUCHE='MOY',
        NOM_CHAM=('SIGM_ELNO', ),
        NUME_COUCHE=1
    ),
    RESULTAT=result
)

stress = CALC_CHAMP(
    reuse=stress,
    CONTRAINTE=('SIGM_NOEU', ),
    RESULTAT=stress
)

strain = POST_CHAMP(
    EXTR_COQUE=_F(
        NIVE_COUCHE='MOY',
    NOM_CHAM=('EPSI_ELNO', ),
    NUME_COUCHE=1),
    RESULTAT=result
)

strainP = POST_CHAMP(
    EXTR_COQUE=_F(
        NIVE_COUCHE='MOY',
    NOM_CHAM=('EPSP_ELNO', ),
    NUME_COUCHE=1),
    RESULTAT=result
)

IMPR_RESU(
    RESU=(
        _F(
            NOM_CHAM=("DEPL", "EFGE_ELNO", "EFGE_NOEU"),
            NOM_CHAM_MED=("DISP", "GEN_FORCES_ELEM", "GEN_FORCES_NODES"),
            RESULTAT=result,
        ),
        _F(
            NOM_CHAM=("SIGM_ELNO", "SIGM_NOEU"),
            NOM_CHAM_MED=("STRESSES_ELEM", "STRESSES_NODES"),
            RESULTAT=stress,
        ),
        _F(
            NOM_CHAM=("EPSI_ELNO",),
            NOM_CHAM_MED=("STRAINS_ELEM",),
            RESULTAT=strain,
        ),
        _F(
            NOM_CHAM=("EPSP_ELNO",),
            NOM_CHAM_MED=("PLASTIC_STRAINS_ELEM",),
            RESULTAT=strainP,
        ),
    ),
    UNITE=80,
)"""
