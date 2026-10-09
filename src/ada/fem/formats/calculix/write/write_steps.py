from __future__ import annotations

from typing import TYPE_CHECKING

from ada.core.utils import bool2text
from ada.fem.steps import Step, StepEigen, StepImplicitStatic

if TYPE_CHECKING:
    from .writer import DeckContext


def has_load_cases(step: Step) -> bool:
    return step.type == Step.TYPES.STATIC and not isinstance(step, StepEigen) and len(step.load_cases) > 0


def steps_str(steps: list[Step], deck: DeckContext) -> str:
    """Every step, in order: a step of load cases as one ``*STEP`` per case, any other step as one ``*STEP``.

    Each static ``*STEP`` opens with ``*CLOAD, OP=NEW`` and ``*DLOAD, OP=NEW`` (measured, ccx 2.23: that removes what
    came before, also as a line of its own followed by a second ``*CLOAD``) and lists the loads it is solved for: a load
    case its own loads, a general step its own and the ones it carries from the general steps before it
    (:func:`ada.fem.formats.carried_loads.general_step_loads`). Left to carry by themselves, a load written as nodal
    ``*CLOAD`` lines would be replaced wherever a later step loads the same node and dof.
    """
    from ada.fem.formats.carried_loads import general_step_loads

    from .write_loads import STAGE

    applied = general_step_loads(steps, STAGE)
    out = []
    for step in steps:
        if has_load_cases(step):
            out += [load_case_step_str(step, lc, deck) for lc in step.load_cases.values()]
            continue
        out.append(step_str(step, deck, loads=applied.get(id(step))))
    return "\n".join(out)


def _loads_str(loads, deck: DeckContext) -> str:
    from .write_loads import load_str

    return "\n".join([load_str(ld, deck) for ld in loads]) if len(loads) > 0 else "** No Loads"


def _outputs_str(step: Step, deck: DeckContext | None = None) -> tuple[str, str]:
    nodal = []
    elem = []
    for fi in step.field_outputs:
        nodal += fi.nodal
        elem += fi.element
    nodal_str = "*node file\n" + ", ".join(nodal) if len(nodal) > 0 else "** No nodal output"
    if deck is not None and "RF" in nodal and step.type == Step.TYPES.STATIC:
        nodal_str = reaction_totals_str(deck) + nodal_str
    elem_str = "*el file\n" + ", ".join(elem) if len(elem) > 0 else "** No elem output"
    return nodal_str, elem_str


def reaction_totals_str(deck: DeckContext) -> str:
    """``*NODE PRINT, NSET=<set>, TOTALS=ONLY`` / ``RF`` for every support's node set: ccx writes the sum of the set's
    external forces to the .dat file (``total force (fx,fy,fz) for set <SET>``, ``%13.6E``), read by
    :func:`ada.fem.formats.calculix.results.read_dat.read_reaction_totals`.

    The .frd prints each nodal force to six digits (``%12.5E``), and a sum over the expanded nodes of second-order
    shells -- large opposite forces at corner and midside nodes -- kept only what those digits allow: 1999.8828 N for
    2000 N on 8-node shells (bound 1.08 N). The totals are summed before printing: 999.9992 + 999.9994 N on the same
    model (measured, ccx 2.23; manual 7.99). The sum is the set's *external* force -- reactions plus any load applied
    at its nodes -- and a set on U1 beam nodes is left out (there ccx's RF are the elements' end forces, not
    reactions).
    """
    sets = []
    for bc in deck.bcs:
        fs = bc.fem_set
        if fs.name in sets:
            continue
        if any(getattr(el, "id", None) in deck.u1_elements for no in fs.members for el in getattr(no, "refs", ())):
            continue
        sets.append(fs.name)
    return "".join(f"*Node print, nset={name}, totals=only\nRF\n" for name in sets)


def _bcs_str(step: Step, deck: DeckContext, cases: set[str] | None = None) -> str:
    """The step's own ``Bc``s, a prescribed displacement among them with its values (it carries into later steps, as
    in Abaqus), then -- for a static step standing for the load case names ``cases`` -- the model's prescribed
    displacements (:func:`prescribed_str`)."""
    from ada.fem.formats.prescribed import settlements

    from .writer import bc_str, refuse_on_u1

    own = list(step.bcs.values())
    refused = refuse_on_u1(deck.part, deck.u1_elements, own) if own else frozenset()
    blocks = []
    for bc in own:
        if bc in settlements([bc]) and not any((bc.fem_set.name, d) in refused for d in bc.dofs):
            lines = [f" {bc.fem_set.name}, {d}, {d}, {ccx_value(m)}" for d, m in zip(bc.dofs, bc.magnitudes)]
            blocks.append(f"** Name: {bc.name} Type: prescribed displacement\n*Boundary\n" + "\n".join(lines))
        else:
            blocks.append(bc_str(bc))
    if cases is not None:
        blocks.append(prescribed_str(deck, cases, step=step.name))
    blocks = [b for b in blocks if b]
    return "\n".join(blocks) if blocks else "** No BCs"


def ccx_value(m) -> str:
    from .write_loads import ccx_number

    return ccx_number(0.0 if m is None else m)


def prescribed_str(deck: DeckContext, cases: set[str], **where) -> str:
    """The model's prescribed displacements in a static ``*STEP`` standing for the load case names ``cases``: every
    prescribed dof at its value in one of ``cases``, or at zero (:func:`ada.fem.formats.prescribed.case_values`).

    Every static step lists every prescribed dof because ccx carries a ``*BOUNDARY`` value into the next step and a
    step that does not name the dof would inherit the last one's. Measured, ccx 2.23, a 4 m x 0.5 m x 10 mm plate strip
    in cylindrical bending (S4, quads at 0.125 m), root clamped, its tip edge's dz held in model data and given -0.01 m
    and +0.02 m in two load case steps: root reaction 4.508288 N against ``3 D b delta / L^3`` = 4.507212 N (+2.4e-4)
    and mid-span -3.12455e-3 m against -3.125e-3 m, the second step exactly -2 times the first; ry given 0.01 rad:
    root moment 24.0384 N m against ``D b theta / L`` = 24.03846 N m. Not written on a node of a U1 beam
    (:func:`.writer.refuse_on_u1`).
    """
    from ada.fem.formats.prescribed import case_values

    from .write_loads import STAGE

    if not deck.bcs:
        return ""
    values = case_values(deck.bcs, cases, STAGE, **where)
    lines = [f" {s}, {d}, {d}, {ccx_value(v)}" for (s, d), v in values.items() if (s, d) not in deck.refused]
    if not lines:
        return ""
    return "** Prescribed displacements\n*Boundary\n" + "\n".join(lines)


def load_case_step_str(step: StepImplicitStatic, lc, deck: DeckContext) -> str:
    """One load case of a static step as a linear ``*STEP`` of its own, starting from no load."""
    from ada.fem.formats import conversion_report

    from .write_loads import STAGE

    rep = conversion_report.current()
    first = next(iter(step.load_cases.values())) is lc
    if first:
        if step.nl_geom:
            rep.approximated(
                STAGE, "Step", step.name, "a step of load cases is written as linear steps, one per case; nlgeom is not"
            )
        in_cases = {id(ld) for case in step.load_cases.values() for ld in (case.loads or [])}
        for load in step.loads:
            if id(load) not in in_cases:
                rep.omitted(
                    STAGE, "Load", load.name, "a load of a step with load cases, in none of them", step=step.name
                )
    nodal_str, elem_str = _outputs_str(step, deck)
    head = f"""*Step, nlgeom=NO, inc={step.total_incr}
*Static
 {step.init_incr}, {step.total_time}, {step.min_incr}, {step.max_incr}"""
    return f"""**
** STEP: {step.name}  LOAD CASE: {lc.name}
**
{head}
**
** BOUNDARY CONDITIONS
**
{_bcs_str(step, deck, {lc.name})}
**
** LOADS (this load case only)
**
*Cload, OP=NEW
*Dload, OP=NEW
{_loads_str(lc.loads or [], deck)}
**
** OUTPUT REQUESTS
**
{nodal_str}
{elem_str}
*End Step"""


def step_str(step: StepEigen | StepImplicitStatic, deck: DeckContext, loads=None):
    """One ``*STEP``; ``loads``, for a static step, the loads it is solved for, written after ``OP=NEW``."""
    from .writer import interactions_str

    if loads is None:
        lstr = _loads_str(step.loads, deck)
    else:
        lstr = "*Cload, OP=NEW\n*Dload, OP=NEW\n" + _loads_str(loads, deck)

    int_str = (
        "\n".join([interactions_str(interact) for interact in step.interactions.values()])
        if len(step.interactions.values()) > 0
        else "** No Interactions"
    )

    nodal_str, elem_str = _outputs_str(step, deck)

    step_type_map = {
        Step.TYPES.STATIC: static_step,
        Step.TYPES.EIGEN: eigen_step,
    }

    step_str_writer = step_type_map.get(step.type, None)
    if step_str_writer is None:
        raise ValueError(f'Currently unsupported Step Type "{step.type}"')

    step_type_str = step_str_writer(step)
    return f"""**
** STEP: {step.name}
**
{step_type_str}
**
** BOUNDARY CONDITIONS
**
{_bcs_str(step, deck, {step.name} if step.type == Step.TYPES.STATIC else None)}
**
** LOADS
**
{lstr}
**
** INTERACTIONS
**
{int_str}
**
** OUTPUT REQUESTS
**
{nodal_str}
{elem_str}
*End Step"""


def static_step(step: StepImplicitStatic):
    return f"""*Step, nlgeom={bool2text(step.nl_geom)}, inc={step.total_incr}
*Static
 {step.init_incr}, {step.total_time}, {step.min_incr}, {step.max_incr}"""


def eigen_step(step: StepEigen):
    return f"""*Step, name={step.name}
*Frequency
 {step.num_eigen_modes}"""
