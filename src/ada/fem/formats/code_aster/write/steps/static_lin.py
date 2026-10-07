from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem import StepImplicitStatic

from ..names import concept_name
from ..write_loads import write_load
from .fields import create_field_output_str

if TYPE_CHECKING:
    from ada.api.spatial import Part
    from ada.fem import Load


def step_cases(step: StepImplicitStatic, applied=()) -> list[tuple[str, list[Load]]]:
    """The cases a static step is solved for: its load cases, or the step's own loads as one case.

    A load of a step that has load cases but is in none of them is reported, as the Abaqus and Calculix writers do.
    """
    from ada.fem.formats import conversion_report

    from ..write_loads import STAGE

    if len(step.load_cases) == 0:
        return [(step.name, _distinct(list(applied) + list(step.loads)))]
    in_cases = {id(ld) for lc in step.load_cases.values() for ld in (lc.loads or [])}
    for load in step.loads:
        if id(load) not in in_cases:
            conversion_report.current().omitted(
                STAGE, "Load", load.name, "a load of a step with load cases, in none of them", step=step.name
            )
    cases = []
    for lc in step.load_cases.values():
        if not lc.loads:
            # A settlement-only case of a GeniE model, say: MACRO_ELAS_MULT takes no case without a load.
            conversion_report.current().omitted(
                STAGE, "CAS_CHARGE", lc.name, "a load case with no load is not solved", step=step.name
            )
            continue
        cases.append((lc.name, _distinct(lc.loads)))
    return cases


def _nom_cas(name: str, k: int) -> str:
    """A case's ``NOM_CAS``, at most 16 characters (a K16): its name, or ``case<k>`` for a longer one."""
    return name if len(name) <= 16 and "'" not in name else f"case{k}"


def _distinct(loads) -> list[Load]:
    seen, out = set(), []
    for ld in loads:
        if id(ld) not in seen:
            seen.add(id(ld))
            out.append(ld)
    return out


def _distributed(load: Load) -> bool:
    """Whether a load is a distributed load to Code_Aster's beam stress calculation (CALCULEL2_92). A pressure is
    counted too: whether ``FORCE_COQUE`` counts on a model with beams is not established, and counting it can only
    leave stresses uncomputed (reported), never stop the run."""
    from ada.fem import LoadLine

    from ..write_loads import _line_load_groups

    if load.type in (load.TYPES.GRAVITY, load.TYPES.ACC, load.TYPES.PRESSURE):
        return True
    if isinstance(load, LoadLine):
        return len(_line_load_groups(load)[0]) > 0
    return False


def _stress_cases(step, part, cases) -> list[int] | None:
    """The 1-based cases whose stresses and element forces ``CALC_CHAMP`` can compute; ``None`` for all of them.

    Code_Aster computes a beam model's stresses and element forces with one distributed load at most -- measured,
    18.1.8: ``CONTRAINTE=("SIEF_ELNO",)`` over a result whose loads held several stopped at <CALCULEL2_92> ("votre
    chargement contient plus d'une charge répartie ... modèles de poutre"), where ``FORCE=("REAC_NODA",)`` ran. A case
    with more is reported and keeps its displacements and reactions.
    """
    from ada.fem.formats import conversion_report

    from ..write_loads import STAGE

    if len(part.fem.sections.lines) == 0:
        return None
    ok = []
    for k, (name, case_loads) in enumerate(cases, start=1):
        if sum(1 for ld in case_loads if _distributed(ld)) > 1:
            conversion_report.current().omitted(
                STAGE,
                "CALC_CHAMP",
                name,
                "stresses and element forces of a load case with more than one distributed load on a beam model; "
                "Code_Aster computes those with one at most (CALCULEL2_92). Displacements and reactions are written",
                step=step.name,
            )
            continue
        ok.append(k)
    return None if len(ok) == len(cases) else ok


def step_static_lin_str(step: StepImplicitStatic, part: Part, result: str = "result", applied=()) -> str:
    """A linear static step into the result concept ``result``, every load of it applied.

    A step without load cases is one ``MECA_STATIQUE`` with all its loads under ``EXCIT``. Only ``step.loads[0]`` used
    to go there, so a step with two loads was solved for its first.

    A step of load cases is one ``MACRO_ELAS_MULT`` (U4.51.02), Code_Aster's command for several elastic load cases:
    one ``CAS_CHARGE`` per case with that case's loads, the supports in ``CHAR_MECA_GLOBAL``, each case solved on its
    own as Sesam solves a load case. It stores each case's own loads, so the stress calculation can be asked for case
    by case (``NUME_ORDRE``), which one ``MECA_STATIQUE`` over all the cases does not allow: its result holds every
    case's loads and a ``CALC_CHAMP`` given other ones may not reuse it (<CALCULEL_39>, measured). ``OPTION='SANS'``
    because the default ``SIEF_ELGA`` per case stops at <CALCULEL2_92> on a case with two distributed beam loads.
    Case ``i`` is order number ``i`` of the result; in the MED file each case has ``PDT = 999.999`` and its order in
    ``NDT``. Measured with Code_Aster 18.1.8 on the concept-loads beam: the same displacements and reactions as one
    ``MECA_STATIQUE`` with an instant and a ``FONC_MULT`` per case, to every printed digit.
    """
    from ada.fem.exceptions.model_definition import (
        NoBoundaryConditionsApplied,
        NoLoadsApplied,
    )

    from ..write_constraints import get_charge_names, has_cara_elem

    cases = step_cases(step, applied)
    loads = _distinct([ld for _, case_loads in cases for ld in case_loads])
    if len(loads) == 0:
        raise NoLoadsApplied(f"No loads are applied in step '{step}'")

    charges = get_charge_names(part)
    if len(charges) == 0:
        raise NoBoundaryConditionsApplied("No boundary condition is found for the specified model")

    load_str = "\n".join(write_load(ld) for ld in loads)

    has_shells_or_beams = has_cara_elem(part)
    sec_str = "\n    CARA_ELEM=element," if has_shells_or_beams else ""
    # MECA_STATIQUE auto-populates SIEF_ELGA, which carries sub-points on
    # shell/beam elements (DKT, POU_D_E, ...). IMPR_RESU then needs
    # CARA_ELEM in the RESU block to print those fields to MED — without it
    # Code Aster raises MED2_14 the moment it tries to dump SIEF_ELGA.
    resu_cara_str = ", CARA_ELEM=element" if has_shells_or_beams else ""

    stress_cases = _stress_cases(step, part, cases)
    if stress_cases is None:
        targets = None
    elif len(stress_cases) == 0:
        targets = []
    else:
        targets = [f"\n        NUME_ORDRE=({', '.join(str(k) for k in stress_cases)},),"]

    if len(step.load_cases) == 0:
        excit = "".join(f"_F(CHARGE={name})," for name in charges)
        excit += "".join(f"_F(CHARGE={concept_name(ld, 'load')})," for ld in loads)
        solve = f"""{result} = MECA_STATIQUE(
    MODELE=model,
    CHAM_MATER=material,{sec_str}
    EXCIT=({excit})
)"""
        if targets is not None:  # one case: computed whole, or not at all
            targets = None if targets else []
    else:
        rows = "\n".join(
            f"        _F(NOM_CAS='{_nom_cas(name, k)}', CHAR_MECA=({''.join(concept_name(ld, 'load') + ',' for ld in lds)}), "
            f"OPTION='SANS'),"
            for k, (name, lds) in enumerate(cases, start=1)
        )
        order = "\n".join(f"#   {k}: {name}" for k, (name, _) in enumerate(cases, start=1))
        solve = f"""# Load cases of step {step.name}, by order number:
{order}
{result} = MACRO_ELAS_MULT(
    MODELE=model,
    CHAM_MATER=material,{sec_str}
    CHAR_MECA_GLOBAL=({''.join(f'{name},' for name in charges)}),
    CAS_CHARGE=(
{rows}
    ),
)"""

    field_str = create_field_output_str(step, part, result, targets)

    return f"""
{load_str}

{solve}

{field_str}

IMPR_RESU(
    RESU=_F(RESULTAT={result}{resu_cara_str}),
    UNITE=80
)

"""
