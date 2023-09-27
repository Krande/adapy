from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem import StepEigen

if TYPE_CHECKING:
    from ada.api.spatial import Part


def step_eig_str(step: StepEigen, part: Part) -> str:
    bcs = part.fem.bcs

    assembly = part.get_assembly()
    if part != assembly:
        for bc in assembly.fem.bcs:
            if bc not in bcs:
                bcs.append(bc)

    if len(bcs) > 1 or len(bcs) == 0:
        raise NotImplementedError(f"Number of BC sets {len(bcs)=} is for now limited to 1 for eigenfrequency analysis")

    eig_map = dict(sorensen="SORENSEN", lanczos="TRI_DIAG")
    eig_type = step.metadata.get("eig_method", "sorensen")
    eig_method = eig_map[eig_type]

    sec_str = ""
    if len(part.fem.sections.lines) > 0 or len(part.fem.sections.shells) > 0:
        sec_str = "\n    CARA_ELEM=element,"

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
    CHAM_MATER=material,{sec_str}
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
