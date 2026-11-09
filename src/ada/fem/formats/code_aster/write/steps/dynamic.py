"""An implicit dynamic step (:class:`~ada.fem.StepImplicitDynamic`) as Code_Aster's ``DYNA_NON_LINE``.

This wrote nothing: ``step_dynamic_str`` returned an empty string, so a dynamic step was left out of the deck without
a word (and its loads with it). Now, as Abaqus' ``*Dynamic`` is an implicit time integration of the full equations of
motion (U4.53.01):

* ``DYNA_NON_LINE`` over the step's time ``0 .. total_time`` at a fixed increment ``init_incr``, Newmark's average
  acceleration (``SCHEMA='NEWMARK'``, no numerical damping) -- or HHT with ``step.options.CALCULIX.alpha`` when given;
* every load times its amplitude (``FONC_MULT``, a ``DEFI_FONCTION`` of the instant ``INST``, held at its ends --
  Abaqus' step-time amplitude), or applied whole from the start without one: a step load;
* the supports in force in the step; the elastic relation, small displacements (large ones are the static nonlinear
  step's ``steps.static_nonlin``);
* displacements, velocities and accelerations printed at every instant (``<r>__DEPL`` ...), the result named
  ``dyn``, ``dyn2`` ... (``write_steps.result_names``).

The step starts from rest: an Abaqus dynamic step after a static one starts from that step's state, which is reported
when there is one before it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem.steps import StepImplicitDynamic

if TYPE_CHECKING:
    import ada


def amplitude_function_str(name: str, amplitude, total_time: float) -> str:
    """``DEFI_FONCTION`` of the instant: the amplitude's (time, value) points, held at its first and last value
    outside them; a constant 1 over the step without one."""
    if amplitude is None:
        values = f"0.0, 1.0, {float(total_time)!r}, 1.0"
    else:
        values = ", ".join(f"{float(x)!r}, {float(y)!r}" for x, y in zip(amplitude.x, amplitude.y))
    return f"""{name} = DEFI_FONCTION(
    NOM_PARA='INST', VALE=({values}), PROL_GAUCHE='CONSTANT', PROL_DROITE='CONSTANT'
)"""


def step_dynamic_str(step: StepImplicitDynamic, part: ada.Part, result: str = "dyn", loads=None) -> str:
    from ada.fem.exceptions.model_definition import NoBoundaryConditionsApplied
    from ada.fem.formats import conversion_report

    from ..write_constraints import has_cara_elem, step_charges
    from ..write_loads import STAGE, write_load
    from ..write_temperature import mechanical
    from .buckle import _BOUND

    supports = step_charges(part, step)
    if len(supports.names) == 0:
        raise NoBoundaryConditionsApplied(f"dynamic step {step.name!r}: no boundary condition")
    total = float(step.total_time)
    dt = float(step.init_incr)
    if not 0.0 < dt <= total:
        raise ValueError(f"dynamic step {step.name!r}: its increment {dt} is not in (0, {total}]")
    loads = mechanical(step.loads if loads is None else loads)
    defs, excit = [], [f"_F(CHARGE={n})," for n in supports.names]
    for k, ld in enumerate(loads, start=1):
        s = write_load(ld, amplitude_written=True)
        (name,) = [m.group(1) for m in [_BOUND.search(s)] if m]
        fn = f"{result}_f{k}"
        defs += [s, amplitude_function_str(fn, getattr(ld, "amplitude", None), total)]
        excit.append(f"_F(CHARGE={name}, FONC_MULT={fn}),")
    sec = "\n    CARA_ELEM=element," if has_cara_elem(part) else ""
    alpha = getattr(getattr(step.options, "CALCULIX", None), "alpha", None)
    scheme = "SCHEMA='NEWMARK', FORMULATION='DEPLACEMENT'"
    if alpha is not None:
        scheme = f"SCHEMA='HHT', ALPHA={float(alpha)!r}, MODI_EQUI='NON', FORMULATION='DEPLACEMENT'"
    assembly = part.get_assembly()
    from ..write_steps import all_steps

    steps = all_steps(assembly)
    if steps and steps[0] is not step:
        conversion_report.current().approximated(
            STAGE, "Step", step.name, "a dynamic step starts from rest, not from the state the steps before it leave"
        )
    n = max(1, round(total / dt))
    excit_str = "".join(excit)
    return f"""
{supports.definitions}
{chr(10).join(defs)}

# Implicit dynamics of step {step.name}: {n} increments of {dt!r} s
{result}_li = DEFI_LIST_REEL(DEBUT=0.0, INTERVALLE=_F(JUSQU_A={total!r}, NOMBRE={n}))
{result} = DYNA_NON_LINE(
    MODELE=model,
    CHAM_MATER=material,{sec}
    EXCIT=({excit_str}),
    COMPORTEMENT=_F(RELATION='ELAS', DEFORMATION='PETIT', TOUT='OUI'),
    INCREMENT=_F(LIST_INST={result}_li),
    SCHEMA_TEMPS=_F({scheme}),
)
{result} = CALC_CHAMP(reuse={result}, RESULTAT={result}, FORCE=('REAC_NODA',))
IMPR_RESU(RESU=_F(RESULTAT={result}, NOM_CHAM=('DEPL', 'VITE', 'ACCE', 'REAC_NODA')), UNITE=80)
"""
