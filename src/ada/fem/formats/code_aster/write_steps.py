from typing import TYPE_CHECKING, Union

from ada.fem import StepEigen, StepImplicit

from .write_loads import write_load

if TYPE_CHECKING:
    from ada.concepts.levels import Part


def step_static_str(step: StepImplicit, part: "Part") -> str:
    from ada.fem.exceptions.model_definition import (
        NoBoundaryConditionsApplied,
        NoLoadsApplied,
    )

    load_str = "\n".join(list(map(write_load, step.loads)))
    if len(step.loads) == 0:
        raise NoLoadsApplied(f"No loads are applied in step '{step}'")
    load = step.loads[0]
    all_boundary_conditions = part.get_assembly().fem.bcs + part.fem.bcs
    if len(all_boundary_conditions) == 0:
        raise NoBoundaryConditionsApplied("No boundary condition is found for the specified model")

    bc_str = ""
    for bc in all_boundary_conditions:
        bc_str += f"_F(CHARGE={bc.name}),"

    if step.nl_geom is False:
        return f"""
{load_str}

result = MECA_STATIQUE(
    MODELE=model,
    CHAM_MATER=material,
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
    else:
        return f"""
{load_str}

timeReel = DEFI_LIST_REEL(DEBUT=0.0, INTERVALLE=_F(JUSQU_A=1.0, NOMBRE=10))
timeInst = DEFI_LIST_INST(METHODE="AUTO", DEFI_LIST=_F(LIST_INST=timeReel))
rampFunc = DEFI_FONCTION(NOM_PARA="INST", VALE=(0.0, 0.0, 1.0, 1.0))

result = STAT_NON_LINE(
    MODELE=model,
    CHAM_MATER=material,
    CARA_ELEM=element,
    COMPORTEMENT=(_F(DEFORMATION="PETIT", RELATION="VMIS_ISOT_TRAC", TOUT="OUI")),
    CONVERGENCE=_F(ARRET="OUI", ITER_GLOB_MAXI=8,),
    EXCIT=({bc_str}_F(CHARGE={load.name}, FONC_MULT=rampFunc)),
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
    NUME_COUCHE=1),
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


def step_eig_str(step: StepEigen, part: "Part") -> str:
    bcs = part.fem.bcs + part.get_assembly().fem.bcs

    if len(bcs) > 1 or len(bcs) == 0:

        raise NotImplementedError("Number of BC sets is for now limited to 1 for eigenfrequency analysis")

    eig_map = dict(sorensen="SORENSEN", lanczos="TRI_DIAG")
    eig_type = step.metadata.get("eig_method", "sorensen")
    eig_method = eig_map[eig_type]

    # TODO: Add check for second order shell elements. If exists add conversion of results back from TRI7 to TRI6
    _ = """
    model_0 = AFFE_MODELE(
    AFFE=(
        _F(GROUP_MA=sh_2nd_order_sets, PHENOMENE='MECANIQUE', MODELISATION='MEMBRANE',),
    ),
    MAILLAGE=mesh
)

modes_0 = PROJ_CHAMP(
    MODELE_1=model,
    MODELE_2=model_0,
    RESULTAT=modes
)"""

    bc = bcs[0]
    return f"""
#modal analysis
ASSEMBLAGE(
    MODELE=model,
    CHAM_MATER=material,
    CARA_ELEM=element,
    CHARGE={bc.name},
    NUME_DDL=CO('dofs_eig'),
    MATR_ASSE = (
        _F(MATRICE=CO('stiff'), OPTION ='RIGI_MECA',),
        _F(MATRICE=CO('mass'), OPTION ='MASS_MECA', ),
    ),
)
# Using Subspace Iteration method ('SORENSEN' AND 'PLUS_PETITE')
# See https://www.code-aster.org/V2/UPLOAD/DOC/Formations/01-modal-analysis.pdf for more information
#

modes = CALC_MODES(
    CALC_FREQ=_F(NMAX_FREQ={step.num_eigen_modes}, ) ,
    SOLVEUR_MODAL=_F(METHODE='{eig_method}'),
    MATR_MASS=mass,
    MATR_RIGI=stiff,
    OPTION='PLUS_PETITE',
    VERI_MODE=_F(STOP_ERREUR='NON')
)



IMPR_RESU(
    RESU=_F(RESULTAT=modes, TOUT_CHAM='OUI'),
    UNITE=80
)
"""


def create_step_str(step: Union[StepEigen, StepImplicit], part: "Part") -> str:
    st = StepEigen.TYPES
    step_map = {st.STATIC: step_static_str, st.EIGEN: step_eig_str}

    step_writer = step_map.get(step.type, None)

    if step_writer is None:
        raise NotImplementedError(f'Step type "{step.type}" is not yet supported')

    return step_writer(step, part)
