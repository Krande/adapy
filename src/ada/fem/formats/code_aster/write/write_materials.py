from __future__ import annotations

from itertools import chain
from typing import TYPE_CHECKING

from ada.config import logger
from ada.materials.concept import Material

if TYPE_CHECKING:
    from ada.api.spatial import Assembly


def materials_str(assembly: Assembly) -> str:
    all_mat = chain.from_iterable([p.materials for p in assembly.get_all_parts_in_assembly(True)])
    all_mat_unique = {x.name: x for x in all_mat}
    nl_geom = False
    for x in assembly.fem.get_all_steps():
        if x.nl_geom is True:
            nl_geom = True

    return "\n".join([material_str(mat, nl_geom) for mat in all_mat_unique.values()])


def material_str(material: Material, nl_geom) -> str:
    from ada.core.utils import NewLine

    # Bi-linear hardening ECRO_LINE=_F(D_SIGM_EPSI=2.0e06, SY=2.35e06,)

    model = material.model
    nl = NewLine(3, suffix="	")
    has_plasticity = model.plasticity_model is not None and model.plasticity_model.eps_p is not None
    if has_plasticity and nl_geom is True:
        nl_mat = "nl_mat=(	\n	"
        eps = [e for e in model.plasticity_model.eps_p]
        eps[0] = 1e-5  # Epsilon index=0 cannot be zero
        nl_mat += "".join([f"{e:.4E},{s:.4E}," + next(nl) for e, s in zip(eps, model.plasticity_model.sig_p)]) + ")"
        nl_mat += """
Traction=DEFI_FONCTION(
    NOM_PARA='EPSI', NOM_RESU='SIGM', VALE=nl_mat, INTERPOL='LIN', PROL_DROITE='LINEAIRE', PROL_GAUCHE='CONSTANT'
)"""
        mat_nl_in = ", TRACTION=_F(SIGM=Traction,)"
    else:
        logger.debug(f"No plasticity is defined for material {material.name}")
        nl_mat = ""
        mat_nl_in = ""

    return f"""{nl_mat}

{material.name} = DEFI_MATERIAU(
    ELAS=_F(E={model.E}, NU={model.v}, RHO={model.rho}){mat_nl_in},
)
"""
