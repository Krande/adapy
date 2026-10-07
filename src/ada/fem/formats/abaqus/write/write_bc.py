"""``*Boundary`` and ``*Connector Motion``: model data, and the prescribed displacements of each step.

A displacement ``Bc`` carrying magnitudes is a prescribed displacement (a settlement). Abaqus 2025 refuses one in
model data -- "***ERROR: PRESCRIBED *BOUNDARY MAGNITUDES MUST BE ZERO IN THE MODEL DEFINITION", measured -- so the
model data holds its dofs at zero (it is a support in every step) and the value goes into the steps
(:func:`prescribed_in_step_str`):

* a ``Bc`` naming no load case: its values in every static step;
* a ``Bc`` naming its load case in ``metadata[BC_LOAD_CASE]`` (one per case, as GeniE gives one support a different
  value in each load case): its values in the static step of that name, or holding that load case. In every other
  static step those dofs are written back to zero, so a later step does not inherit an earlier one's settlement --
  Abaqus carries a step's ``*Boundary`` on into the next. A case no static step stands for is reported
  (:func:`report_unstepped_settlements`).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ada.fem import Bc
from ada.fem.constraints import BC_LOAD_CASE

from ..grammar import format_number
from ..mapping import bc_types
from .helper_utils import get_instance_name, render_block

if TYPE_CHECKING:
    from ada import Assembly
    from ada.fem.steps import Step

STAGE = "abaqus writer"

#: The ``Bc`` types whose magnitudes are displacements, i.e. that a ``*Boundary`` holds.
_DISPLACEMENT_TYPES = (Bc.TYPES.DISPL, Bc.TYPES.DISPL_ROT, Bc.TYPES.ENCASTRE)


def abaqus_bc_type(bc_type: str) -> str:
    """The name CAE gives ``bc_type`` in the comment above ``*Boundary`` -- from the one table the
    reader also reads it back through (:func:`..mapping.bc_types`)."""
    return bc_types().to_abaqus(bc_type)


def is_settlement(bc: Bc) -> bool:
    """A displacement ``Bc`` with a nonzero magnitude: a prescribed displacement, not only a support."""
    return bc.type in _DISPLACEMENT_TYPES and any(m not in (None, 0, 0.0) for m in (bc.magnitudes or ()))


def boundary_conditions_str(assembly: "Assembly"):
    """The model data ``*Boundary`` blocks, a prescribed displacement's dofs held at zero.

    A settlement's dofs are left out where another ``Bc`` on the same set already holds them (GeniE's supports
    come as the support's ``Bc`` plus one per load case on the same set): Abaqus 2025 refuses a dof held twice in
    model data and then given a value in a step ("TWO BOUNDARY CONDITIONS WITH CONFLICTING DISPLACEMENTS",
    measured), and takes the same dof held once.
    """
    held: set[tuple[str, object]] = set()
    blocks = []
    for bc in assembly.fem.get_all_bcs():
        skip = {d for d in bc.dofs if (bc.fem_set.name, d) in held} if is_settlement(bc) else set()
        if bc.type in _DISPLACEMENT_TYPES:
            held.update((bc.fem_set.name, d) for d in bc.dofs)
        if skip and skip >= set(bc.dofs):
            continue
        blocks.append(bc_str(bc, True, model_data=True, skip_dofs=skip))
    return "\n".join(blocks)


def bc_str(bc: "Bc", written_on_assembly_level: bool, model_data: bool = False, skip_dofs=frozenset()) -> str:
    """One ``Bc``. In ``model_data`` a prescribed displacement's dofs are held at zero; its values are the
    steps' (see the module docstring). ``skip_dofs`` are left out."""
    params = [] if bc.amplitude is None else [("amplitude", bc.amplitude.name)]

    fem_set = bc.fem_set
    inst_name = get_instance_name(fem_set, written_on_assembly_level)

    aba_type = abaqus_bc_type(bc.type)
    strip = model_data and bc.type in _DISPLACEMENT_TYPES

    lines = []
    for dof, magn in zip(bc.dofs, bc.magnitudes):
        if dof is None or dof in skip_dofs:
            continue
        magn_str = f", {format_number(magn)}" if magn is not None and not strip else ""
        if bc.type in [Bc.TYPES.CONN_DISPL, Bc.TYPES.CONN_VEL] or isinstance(dof, str):
            lines.append(f" {inst_name}, {dof}{magn_str}")
        else:
            lines.append(f" {inst_name}, {dof}, {dof}{magn_str}")

    add_map = {
        Bc.TYPES.CONN_DISPL: ("Connector Motion", [("type", "DISPLACEMENT")]),
        Bc.TYPES.CONN_VEL: ("Connector Motion", [("type", "VELOCITY")]),
    }
    keyword, add_params = add_map.get(bc.type, ("Boundary", []))
    return render_block(keyword, params + add_params, lines or [""], [f"Name: {bc.name} Type: {aba_type}"])


def _load_case(bc: Bc) -> str | None:
    return (bc.metadata or {}).get(BC_LOAD_CASE)


def _step_cases(step: Step) -> set[str]:
    """The load case names a step stands for: its own name and its load cases'."""
    return {step.name, *step.load_cases.keys()}


def _takes_settlements(step: Step) -> bool:
    return step.type == step.TYPES.STATIC


def prescribed_in_step_str(step: Step, bcs) -> str:
    """The prescribed displacements of the model's ``bcs`` in ``step``, as ``*Boundary`` blocks with magnitudes."""
    from ada.fem.formats import conversion_report

    if not _takes_settlements(step):
        return ""
    settlements = [bc for bc in bcs if is_settlement(bc)]
    cases = _step_cases(step)
    out = []
    in_step: dict[tuple[str, int], str] = {}
    for bc in settlements:
        case = _load_case(bc)
        if case is not None and case not in cases:
            continue
        clash = sorted({in_step[(bc.fem_set.name, d)] or "" for d in bc.dofs if (bc.fem_set.name, d) in in_step})
        if clash:
            conversion_report.current().omitted(
                STAGE,
                "*Boundary",
                bc.name,
                "the step stands for two load cases prescribing the same dofs; the first one's values are written",
                step=step.name,
                kept=clash,
            )
            continue
        in_step.update({(bc.fem_set.name, d): case for d in bc.dofs})
        out.append(bc_str(bc, True))

    for bc in settlements:
        # A settlement of another case: back to zero here, unless this step's own case holds the dof.
        if _load_case(bc) is None or _load_case(bc) in cases:
            continue
        dofs = [d for d in bc.dofs if (bc.fem_set.name, d) not in in_step]
        if not dofs:
            continue
        for d in dofs:
            in_step[(bc.fem_set.name, d)] = _load_case(bc)
        inst_name = get_instance_name(bc.fem_set, True)
        lines = [f" {inst_name}, {d}, {d}, 0." for d in dofs]
        out.append(render_block("Boundary", [], lines, [f"Name: {bc.name} at zero: load case {_load_case(bc)}"]))
    return "\n".join(out)


def prescribed_in_case_str(step: Step, case: str, bcs) -> str:
    """The prescribed displacements of the model's ``bcs`` in load case ``case`` of a perturbation ``step``: those
    naming that case, and those naming none. A load case inherits nothing from another, so nothing is reset."""
    from ada.fem.formats import conversion_report

    out = []
    in_case: dict[tuple[str, int], str] = {}
    for bc in bcs:
        if not is_settlement(bc) or _load_case(bc) not in (None, case):
            continue
        clash = sorted({in_case[(bc.fem_set.name, d)] for d in bc.dofs if (bc.fem_set.name, d) in in_case})
        if clash:
            conversion_report.current().omitted(
                STAGE,
                "*Boundary",
                bc.name,
                "a second prescribed displacement of the same dofs in one load case; the first one's values are "
                "written",
                step=step.name,
                load_case=case,
                kept=clash,
            )
            continue
        in_case.update({(bc.fem_set.name, d): bc.name for d in bc.dofs})
        out.append(bc_str(bc, True))
    return "\n".join(out)


def report_unstepped_settlements(assembly: Assembly) -> None:
    """Every load case a prescribed displacement names that no static step stands for: its values are written
    nowhere, and the dofs are held at zero."""
    from ada.fem.formats import conversion_report

    from .write_steps import abaqus_steps

    steps = [s for s in abaqus_steps(assembly) if _takes_settlements(s)]
    stood_for = set().union(*(_step_cases(s) for s in steps)) if steps else set()
    for bc in assembly.fem.get_all_bcs():
        if not is_settlement(bc):
            continue
        case = _load_case(bc)
        if (case is None and not steps) or (case is not None and case not in stood_for):
            conversion_report.current().omitted(
                STAGE,
                "*Boundary",
                bc.name,
                "a prescribed displacement whose load case no static step stands for; Abaqus takes a nonzero "
                "*Boundary only in a step, so its dofs are held at zero",
                load_case=case,
                magnitudes=list(bc.magnitudes),
            )
