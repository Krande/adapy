"""Couplings for Code_Aster.

A coupling (or rigid body) ties its dependent nodes rigidly to a reference node, written as ``LIAISON_SOLIDE``
over the reference node set and the dependent node set. A reference node that belongs to no element has no
dofs in Code_Aster, so each one gets a ``POI1`` cell with a zero-stiffness ``DIS_TR`` discrete element. That
gives it the 6 dofs a support or a load can act on, without adding stiffness or mass.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.config import logger
from ada.fem import Constraint

if TYPE_CHECKING:
    from ada.api.spatial import Part

#: The element group the reference points of all couplings are created in
REF_POINTS_GROUP = "ref_pts"


def get_couplings(part: Part) -> list[Constraint]:
    couplings = []
    for fem in _fems(part):
        for con in fem.constraints.values():
            if con.type not in (Constraint.TYPES.COUPLING, Constraint.TYPES.RIGID_BODY):
                logger.warning(f'Constraint "{con.name}" of type "{con.type}" is not written to Code_Aster')
                continue
            if con not in couplings:
                couplings.append(con)
    return couplings


def get_charge_names(part: Part) -> list[str]:
    """The names of all mechanical loads that make up the supports of the model: boundary conditions and couplings"""
    names = []
    for fem in _fems(part):
        for bc in fem.bcs:
            if bc.name not in names:
                names.append(bc.name)
    names += [con.name for con in get_couplings(part)]
    return names


def has_cara_elem(part: Part) -> bool:
    """Whether the model needs element characteristics (AFFE_CARA_ELEM)"""
    fem = part.fem
    return len(fem.sections.lines) > 0 or len(fem.sections.shells) > 0 or len(get_couplings(part)) > 0


def create_ref_points_mesh_str(part: Part, input_mesh: str, output_mesh: str = "mesh_ref") -> str:
    ref_sets = ", ".join(f"'{con.m_set.name}'" for con in get_couplings(part))
    return f"""{output_mesh} = CREA_MAILLAGE(
    MAILLAGE={input_mesh},
    CREA_POI1=_F(NOM_GROUP_MA='{REF_POINTS_GROUP}', GROUP_NO=({ref_sets},)),
)
"""


def create_ref_points_model_str() -> str:
    return f"_F(GROUP_MA='{REF_POINTS_GROUP}', PHENOMENE='MECANIQUE', MODELISATION='DIS_TR',),"


def create_ref_points_discrete_str() -> str:
    return f"""
        DISCRET=(
            _F(GROUP_MA='{REF_POINTS_GROUP}', CARA='K_TR_D_N', VALE=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)),
            _F(GROUP_MA='{REF_POINTS_GROUP}', CARA='M_TR_D_N', VALE=(0.0,) * 10),
        ),"""


def create_coupling_str(con: Constraint) -> str:
    if con.dofs is not None and sorted(con.dofs) != [1, 2, 3, 4, 5, 6]:
        logger.warning(
            f'Coupling "{con.name}" couples dofs {con.dofs}. Code_Aster writes it as a rigid link in all dofs'
        )
    return f"""{con.name} = AFFE_CHAR_MECA(
    MODELE=model, LIAISON_SOLIDE=_F(GROUP_NO=('{con.m_set.name}', '{con.s_set.name}'))
)"""


def _fems(part: Part):
    yield part.fem
    assembly = part.get_assembly()
    if assembly is not part:
        yield assembly.fem
