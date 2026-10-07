from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem import StepImplicitStatic

from ..names import concept_name
from ..write_loads import write_load
from .fields import create_field_output_str

if TYPE_CHECKING:
    from ada.api.spatial import Part
    from ada.fem import Load


def step_cases(step: StepImplicitStatic, applied=(), bcs=()) -> list[tuple[str, list[Load]]]:
    """The cases a static step is solved for: its load cases, or the step's own loads as one case.

    A load of a step that has load cases but is in none of them is reported, as the Abaqus and Calculix writers do. A
    load case with no load is solved when a prescribed displacement of ``bcs`` names it (a settlement case of a
    GeniE model), and otherwise left out and reported.
    """
    from ada.fem.formats import conversion_report
    from ada.fem.formats.prescribed import names_a_case

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
        if not lc.loads and not names_a_case(bcs, lc.name):
            conversion_report.current().omitted(
                STAGE, "CAS_CHARGE", lc.name, "a load case with no load is not solved", step=step.name
            )
            continue
        cases.append((lc.name, _distinct(lc.loads or [])))
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


def _stress_cases(step, part, cases, together: bool = False) -> list[int] | None:
    """The 1-based cases whose stresses and element forces ``CALC_CHAMP`` can compute; ``None`` for all of them.

    Code_Aster computes a beam model's stresses and element forces with one distributed load at most -- measured,
    18.1.8: ``CONTRAINTE=("SIEF_ELNO",)`` over a result whose loads held several stopped at <CALCULEL2_92> ("votre
    chargement contient plus d'une charge répartie ... modèles de poutre"), where ``FORCE=("REAC_NODA",)`` ran. A case
    with more is reported and keeps its displacements and reactions. ``together``: the cases are instants of one
    ``MECA_STATIQUE``, whose result holds every case's loads, so they are counted over all the cases.
    """
    from ada.fem.formats import conversion_report

    from ..write_loads import STAGE

    if len(part.fem.sections.lines) == 0:
        return None
    every = _distinct([ld for _, case_loads in cases for ld in case_loads])
    ok = []
    for k, (name, case_loads) in enumerate(cases, start=1):
        if sum(1 for ld in (every if together else case_loads) if _distributed(ld)) > 1:
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

    Prescribed displacements (:mod:`ada.fem.formats.prescribed`): a step that gives a prescribed dof a nonzero value
    holds those dofs with a charge of its own instead of :data:`..write_bc.PRESCRIBED_AT_ZERO`. A general step's
    charge carries the values. ``MACRO_ELAS_MULT`` cannot vary a support from case to case: every dualised charge must
    be in ``CHAR_MECA_GLOBAL`` (measured, 18.1.8: a case's own ``DDL_IMPO`` stopped at <ASSEMBLA_45> whether or not
    the global charges also held the dof). So a step of load cases with a nonzero prescribed value in any case is
    solved as one ``MECA_STATIQUE`` with an instant per case (:func:`_instants_str`).
    """
    from ada.fem.exceptions.model_definition import (
        NoBoundaryConditionsApplied,
        NoLoadsApplied,
    )
    from ada.fem.formats.prescribed import case_values

    from ..write_bc import PRESCRIBED_AT_ZERO, prescribed_charge_str
    from ..write_constraints import get_charge_names, has_cara_elem, model_bcs
    from ..write_loads import STAGE

    bcs = model_bcs(part)
    cases = step_cases(step, applied, bcs)
    loads = _distinct([ld for _, case_loads in cases for ld in case_loads])
    if len(step.load_cases) == 0:
        values = [case_values(bcs, {step.name}, STAGE, step=step.name)]
    else:
        values = [case_values(bcs, {name}, STAGE, step=step.name, load_case=name) for name, _ in cases]
    settled = any(v != 0.0 for case in values for v in case.values())
    if len(loads) == 0 and not settled:
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

    instants = settled and len(step.load_cases) > 0
    stress_cases = _stress_cases(step, part, cases, together=instants)
    if stress_cases is None:
        targets = None
    elif len(stress_cases) == 0:
        targets = []
    else:
        targets = [f"\n        NUME_ORDRE=({', '.join(str(k) for k in stress_cases)},),"]

    if settled:
        charges = [c for c in charges if c != PRESCRIBED_AT_ZERO]
    if instants:
        solve = _instants_str(step, result, cases, values, charges, sec_str, bcs)
    elif len(step.load_cases) == 0:
        excit = "".join(f"_F(CHARGE={name})," for name in charges)
        pd_str = ""
        if settled:
            pd_str = prescribed_charge_str(f"{result}_pd", values[0], bcs) + "\n"
            excit += f"_F(CHARGE={result}_pd),"
        excit += "".join(f"_F(CHARGE={concept_name(ld, 'load')})," for ld in loads)
        solve = f"""{pd_str}{result} = MECA_STATIQUE(
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
    printed = _second_order_shell_fields(step, part)

    return f"""
{load_str}

{solve}

{field_str}

IMPR_RESU(
    RESU=_F(RESULTAT={result}{resu_cara_str}{printed}),
    UNITE=80
)

"""


def _second_order_shell_fields(step, part) -> str:
    """``, NOM_CHAM=(...)`` for a model with second-order shells, else nothing.

    adapy writes 6- and 8-node shells as ``COQUE_3D`` on the 7- and 9-node cells ``CREA_MAILLAGE`` makes of them, and
    Code_Aster 18.1.8 cannot print a field with sub-points (layers) on those to MED: "<MED2_20> L'impression pour des
    éléments COQUE avec l'élément support QU9 n'est pas géré" -- measured on every linear static step of such a model,
    including ``test_fem_static_cantilever``'s second-order shells, which passed only because the run's partial MED
    file existed. The stresses (``SIEF_ELGA``, ``SIGM_ELNO``, ``SIEF_ELNO``) are such fields; displacements,
    reactions and generalised forces (``EFGE``) are not. So those three are printed and the stresses are reported
    ``omitted``.
    """
    from ada.fem.formats import conversion_report
    from ada.fem.shapes.definitions import ShellShapes

    from ..write_loads import STAGE

    second = (ShellShapes.TRI6, ShellShapes.QUAD8)
    if not any(el.type in second for el in part.fem.elements):
        return ""
    fields = ["DEPL"]
    if step.field_outputs:
        fields += ["REAC_NODA", "EFGE_ELNO", "EFGE_NOEU"]
    conversion_report.current().omitted(
        STAGE,
        "IMPR_RESU",
        step.name,
        "stresses on second-order shells (COQUE_3D on 7- and 9-node cells) are not printed: Code_Aster cannot write "
        "their layered fields to MED (MED2_20); displacements, reactions and generalised forces are",
    )
    names = ", ".join(f'"{f}"' for f in fields)
    return f", NOM_CHAM=({names},)"


def _function_str(name: str, values) -> str:
    """A function of ``INST`` worth ``values[k - 1]`` at instant ``k`` (and 0 at 0)."""
    pts = ", ".join(f"{float(k)!r}, {float(v)!r}" for k, v in enumerate(values, start=1))
    return f"{name} = DEFI_FONCTION(NOM_PARA='INST', VALE=(0.0, 0.0, {pts}))"


def _instants_str(step, result: str, cases, values, charges, sec_str: str, bcs) -> str:
    """A step of load cases as one ``MECA_STATIQUE``, case ``k`` at instant ``k``: each load under a ``FONC_MULT``
    that is 1 at the instants of the cases holding it and 0 at the others, and each prescribed dof under a charge of
    1 whose ``FONC_MULT`` is that dof's value in each case. Order number ``k`` is case ``k``, as in
    ``MACRO_ELAS_MULT``; in the MED file ``PDT = k``.

    Measured, Code_Aster 18.1.8, a 4 m IPE300 cantilever whose tip dz is prescribed: case 1 a mid-span load of 1 kN
    (tip reaction 312.5 N), case 2 a settlement of -0.01 m (tip reaction -7874.0027753 N = 3 E I delta / L^3 and
    mid-span -0.003125 m), case 3 both (+0.02 m), case 4 a combination 2 x case 2 -- each exactly the closed form.
    """
    from ..write_bc import prescribed_charge_str

    n = len(cases)
    out = [f"{result}_t = DEFI_LIST_REEL(VALE=({', '.join(f'{float(k)!r}' for k in range(1, n + 1))},))"]
    excit = "".join(f"_F(CHARGE={name})," for name in charges)
    loads = _distinct([ld for _, lds in cases for ld in lds])
    for i, ld in enumerate(loads, start=1):
        out.append(_function_str(f"{result}_f{i}", [1.0 if any(ld is x for x in lds) else 0.0 for _, lds in cases]))
        excit += f"_F(CHARGE={concept_name(ld, 'load')}, FONC_MULT={result}_f{i}),"
    for j, key in enumerate(values[0], start=1):
        charge = prescribed_charge_str(f"{result}_p{j}", {key: 1.0}, bcs)
        if not charge:
            continue
        out.append(charge)
        out.append(_function_str(f"{result}_g{j}", [case[key] for case in values]))
        excit += f"_F(CHARGE={result}_p{j}, FONC_MULT={result}_g{j}),"
    order = "\n".join(f"#   {k}: {name}" for k, (name, _) in enumerate(cases, start=1))
    return (
        "\n".join(out)
        + f"""
# Load cases of step {step.name}, by instant (= order number):
{order}
{result} = MECA_STATIQUE(
    MODELE=model,
    CHAM_MATER=material,{sec_str}
    LIST_INST={result}_t,
    EXCIT=({excit})
)"""
    )
