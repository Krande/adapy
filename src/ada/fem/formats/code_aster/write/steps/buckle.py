"""A linear (Euler) buckling step (:class:`~ada.fem.StepBuckling`) as Code_Aster's ``MODE_FLAMB`` (U2.08.04).

The procedure of U2.08.04 section 1, for one reference load:

1. a linear static solve of the step's loads with the supports, ``OPTION='SIEF_ELGA'`` (``<r>_s``);
2. its stress field (``CREA_CHAMP``, ``ELGA_SIEF_R``), and from it the geometric stiffness ``RIGI_GEOM``
   (``CALC_MATR_ELEM``);
3. the material stiffness ``RIGI_MECA`` with the supports, both numbered and assembled on one ``NUME_DDL``;
4. ``CALC_MODES(TYPE_RESU='MODE_FLAMB', OPTION='PLUS_PETITE')``: the ``num_modes`` critical load factors of the
   smallest magnitude, ``(K + mu K_g) x = 0`` -- U2.08.04: "it is the critical load factor mu and not lambda that is
   stored in the result concept".

The modes go to the MED file as the result ``<r>`` (``buckle``, ``buckle2``, ...), and the factors
(``CHAR_CRIT``) to a table on unit 82, ``<name>.buckling.csv``, which the reader gives as
``FEAResult.buckling_factors``.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada.api.spatial import Part
    from ada.fem.steps import StepBuckling

#: The unit the critical load factors are printed to (``execute.write_export_file`` declares it).
BUCKLING_UNIT = 82

_BOUND = re.compile(r"^(\w+)\s*=", re.M)


def step_buckle_str(step: StepBuckling, part: Part, result: str = "buckle") -> str:
    from ada.fem.exceptions.model_definition import (
        NoBoundaryConditionsApplied,
        NoLoadsApplied,
    )

    from ..write_constraints import has_cara_elem, step_charges
    from ..write_loads import write_load

    supports = step_charges(part, step)
    if len(supports.names) == 0:
        raise NoBoundaryConditionsApplied(f"buckling step {step.name!r}: no boundary condition")
    if len(step.loads) == 0:
        raise NoLoadsApplied(f"buckling step {step.name!r}: no reference load")
    load_strs = [write_load(ld) for ld in step.loads]
    load_names = [m.group(1) for s in load_strs for m in [_BOUND.search(s)] if m]
    sec = "\n    CARA_ELEM=element," if has_cara_elem(part) else ""
    excit = "".join(f"_F(CHARGE={n})," for n in [*supports.names, *load_names])
    held = ", ".join(supports.names)
    return f"""
{supports.definitions}
{chr(10).join(load_strs)}

# Linear buckling of step {step.name} (U2.08.04): the reference load's stresses, their geometric stiffness, and the
# critical load factors mu of (K + mu Kg) x = 0
{result}_s = MECA_STATIQUE(
    MODELE=model,
    CHAM_MATER=material,{sec}
    EXCIT=({excit}),
    OPTION='SIEF_ELGA',
)
{result}_sg = CREA_CHAMP(
    TYPE_CHAM='ELGA_SIEF_R', OPERATION='EXTR', RESULTAT={result}_s, NOM_CHAM='SIEF_ELGA', NUME_ORDRE=1
)
{result}_ge = CALC_MATR_ELEM(OPTION='RIGI_GEOM', MODELE=model,{sec} SIEF_ELGA={result}_sg)
{result}_ke = CALC_MATR_ELEM(OPTION='RIGI_MECA', MODELE=model, CHAM_MATER=material,{sec} CHARGE=({held},))
{result}_nu = NUME_DDL(MATR_RIGI={result}_ke)
{result}_k = ASSE_MATRICE(MATR_ELEM={result}_ke, NUME_DDL={result}_nu)
{result}_g = ASSE_MATRICE(MATR_ELEM={result}_ge, NUME_DDL={result}_nu)
{result} = CALC_MODES(
    MATR_RIGI={result}_k,
    MATR_RIGI_GEOM={result}_g,
    TYPE_RESU='MODE_FLAMB',
    OPTION='PLUS_PETITE',
    CALC_CHAR_CRIT=_F(NMAX_CHAR_CRIT={step.num_modes}),
    SOLVEUR_MODAL=_F(METHODE='SORENSEN'),
    VERI_MODE=_F(STOP_ERREUR='NON'),
)
IMPR_RESU(RESU=_F(RESULTAT={result}), UNITE=80)
{result}_t = RECU_TABLE(CO={result}, NOM_PARA=('NUME_MODE', 'CHAR_CRIT'))
IMPR_TABLE(TABLE={result}_t, UNITE={BUCKLING_UNIT}, SEPARATEUR=',', FORMAT='TABLEAU')
"""
