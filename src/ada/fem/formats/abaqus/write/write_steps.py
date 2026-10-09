from typing import TYPE_CHECKING, Union

from ada.core.utils import bool2text
from ada.fem.steps import (
    Step,
    StepEigen,
    StepEigenComplex,
    StepExplicit,
    StepImplicitStatic,
    StepSteadyState,
)

from ..grammar import format_number, render_keyword
from .helper_utils import get_instance_name, include_str, render_block
from .templates import step_inp_str

if TYPE_CHECKING:
    from ada import FEM

_step_types = Union[StepEigen, StepExplicit, StepImplicitStatic, StepSteadyState, StepEigenComplex]


def abaqus_steps(assembly) -> list[Step]:
    """The steps the deck holds: the assembly's, then each part FEM's.

    A part's FEM carries the step its concept load cases became (``Part.to_fem_obj``), and the Sesam writer has
    always taken a part's steps; this writer took the assembly's only, so a part's step -- its loads and load cases
    with it -- was left out without a word.
    """
    return list(assembly.fem.steps) + [s for p in assembly.get_all_subparts() for s in p.fem.steps]


def main_step_inp_str(step: _step_types) -> str:
    return include_str(f"core_input_files\\step_{step.name}.inp")


def write_step(step_in: _step_types, analysis_dir):
    step_str = abaqus_step_str(step_in)
    with open(analysis_dir / "core_input_files" / f"step_{step_in.name}.inp", "w") as d:
        d.write(step_str)
        if "*End Step" not in step_str:
            d.write(render_keyword("End Step"))


def abaqus_step_str(step: _step_types):
    if "aba_inp" in step.metadata.keys():
        return step.metadata["aba_inp"]

    app_str = step.metadata["append"] if "append" in step.metadata.keys() else "**"

    st = Step.TYPES
    step_map = {
        st.STATIC: static_step_str,
        st.DYNAMIC: dynamic_implicit_str,
        st.EXPLICIT: explicit_str,
        st.EIGEN: eigenfrequency_str,
        st.STEADY_STATE: steady_state_response_str,
        st.COMPLEX_EIG: complex_eig_str,
    }
    # By class first: StepEigenComplex inherits StepEigen.__init__, which gives it the EIGEN type,
    # so dispatching on the type wrote every complex-frequency step as a plain *Frequency.
    step_str_writer = complex_eig_str if isinstance(step, StepEigenComplex) else step_map.get(step.type, None)
    if step_str_writer is None:
        raise ValueError(f"Unrecognized step type {step.type}.")

    if _has_load_cases(step):
        return load_case_step_str(step, app_str)

    step_input_str = step_str_writer(step)

    return step_inp_str.format(
        name=step.name,
        step_input=step_input_str,
        bcs_str=all_bc_str(step),
        load_str=load_str(step),
        int_str=interactions_str(step),
        restart_request_str=restart_request_str(step),
        hist_output_str=hist_output_str(step),
        field_output_str=field_output_str(step),
        app_str=app_str,
    )


def _has_load_cases(step) -> bool:
    return step.type == Step.TYPES.STATIC and not isinstance(step, StepEigen) and len(step.load_cases) > 0


def load_case_step_str(step: StepImplicitStatic, app_str: str = "**") -> str:
    """A static step holding load cases: a linear perturbation step with one ``*Load Case`` block per case.

    Each Sesam load case is solved on its own, and so is each ``*Load Case`` of a perturbation step -- a general
    static step carries its loads into the next, and writing every case's loads into one step, as this did, summed
    them. Measured with Abaqus 2025 on a 4 m B31 beam: the model data's ``*Boundary`` holds in every load case
    (reactions 2000 + 2000 N for 1000 N/m), a ``*Boundary`` with a value inside a load case prescribes that dof in
    that case only even when model data holds it (the end at -0.01, a rigid-body rotation of 2.5e-3), and no case
    inherits another's loads. A prescribed displacement goes into the case its ``Bc`` names (``BC_LOAD_CASE``), one
    naming none into every case. The step's own boundary conditions are written in every case. A perturbation step
    is linear: a step asking for geometric nonlinearity is reported.
    """
    from ada.fem.formats import conversion_report

    from .write_bc import STAGE, bc_str, prescribed_in_case_str
    from .write_loads import load_str as one_load_str

    rep = conversion_report.current()
    if step.nl_geom:
        rep.approximated(
            STAGE, "Step", step.name, "a step of load cases is a linear perturbation step; nlgeom is not written"
        )
    in_cases = {id(ld) for lc in step.load_cases.values() for ld in (lc.loads or [])}
    for load in step.loads:
        if id(load) not in in_cases:
            rep.omitted(STAGE, "Load", load.name, "a load of a step with load cases, in none of them", step=step.name)

    model_bcs = list(step.parent.get_all_bcs()) if getattr(step.parent, "parent", None) is not None else []
    step_bcs = [bc_str(bc, True) for bc in step.bcs.values()]
    cases = []
    for lc in step.load_cases.values():
        body = step_bcs + [prescribed_in_case_str(step, lc.name, model_bcs)]
        body += [one_load_str(load) for load in (lc.loads or [])]
        body = [b.strip("\n") for b in body if b]
        cases.append(
            render_keyword("Load Case", [("name", lc.name)])
            + ("\n".join(body) + "\n" if body else "")
            + render_keyword("End Load Case")
        )

    head = render_keyword(
        "Step", [("name", step.name), ("nlgeom", "NO"), ("perturbation", None)], (), _step_banner(step)
    ) + render_keyword("Static")
    return step_inp_str.format(
        name=step.name,
        step_input=head.rstrip(),
        bcs_str="** In the load cases",
        load_str="".join(cases).rstrip(),
        int_str=interactions_str(step),
        restart_request_str=restart_request_str(step),
        hist_output_str=hist_output_str(step),
        field_output_str=field_output_str(step),
        app_str=app_str,
    )


def constraint_control(fem: "FEM"):
    constraint_ctrl_on = True
    for step in fem.steps:
        if type(step) is StepExplicit:
            constraint_ctrl_on = False
    return "**" if constraint_ctrl_on is False else render_block("constraint controls", [("print", "yes")])


def hist_output_str(step: _step_types):
    from .write_output_requests import hist_output_str

    return "\n".join([hist_output_str(hs) for hs in step.hist_outputs]) if len(step.hist_outputs) > 0 else "**"


def field_output_str(step: _step_types):
    from .write_output_requests import field_output_str

    return "\n".join([field_output_str(fs) for fs in step.field_outputs]) if len(step.field_outputs) > 0 else "**"


def interactions_str(step: _step_types):
    from .write_interactions import interaction_str

    if len(step.interactions) == 0:
        return "** No Interactions"
    return "\n".join([interaction_str(interact) for interact in step.interactions.values()])


def all_bc_str(step: _step_types):
    from .write_bc import bc_str, prescribed_in_step_str

    # The model's prescribed displacements go into the steps: Abaqus takes no nonzero *Boundary in model data.
    model_bcs = list(step.parent.get_all_bcs()) if getattr(step.parent, "parent", None) is not None else []
    settled = prescribed_in_step_str(step, model_bcs)
    if len(step.bcs) == 0:
        return settled or "** No BCs"

    bcstr = ""
    for bcid, bc_ in step.bcs.items():
        bcstr += "\n" if "\n" not in bcstr[-2:] != "" else ""
        bcstr += bc_str(bc_, True)

    return bcstr + ("\n" + settled if settled else "")


def load_str(step: _step_types):
    from .write_loads import load_str

    if len(step.loads) == 0:
        return "** No Loads"

    return "\n".join([load_str(load) for load in step.loads])


def _opt(value) -> str:
    """An optional data field: blank when unset, which Abaqus reads as its default (a step read
    from a deck that left a field blank has None there, and ``None`` is not a number)."""
    return "" if value is None else str(value)


def restart_request_str(step: _step_types):
    solver_options = step.options.ABAQUS
    if solver_options.restart_int is None:
        return "** No Restart Requests"
    return render_block("Restart", [("write", None), ("frequency", solver_options.restart_int)])


def dynamic_implicit_str(step: StepImplicitStatic):
    step_params = [("name", step.name), ("nlgeom", bool2text(step.nl_geom)), ("inc", step.total_incr)]
    dynamic_params = [("application", step.dyn_type), ("INITIAL", bool2text(step.options.ABAQUS.init_accel_calc))]
    data = f"{_opt(step.init_incr)},{_opt(step.total_time)},{_opt(step.min_incr)}, {_opt(step.max_incr)}"
    return render_keyword("Step", step_params) + render_block("Dynamic", dynamic_params, [data])


def explicit_str(step: StepExplicit):
    # No *Bulk Viscosity: it was hard-coded to 0.06, 1.2 -- Abaqus's own defaults, so writing it
    # changed nothing, and no step attribute stood behind it for a reader to give back.
    return render_keyword("Step", [("name", step.name), ("nlgeom", bool2text(step.nl_geom))]) + render_block(
        "Dynamic", [("Explicit", None)], [f", {_opt(step.total_time)}"]
    )


def static_step_str(step: StepImplicitStatic):
    solver_options = step.options.ABAQUS
    stabilize = solver_options.stabilize
    static_params = [] if stabilize is None else stabilize.to_params()

    step_params = [
        ("name", step.name),
        ("nlgeom", bool2text(step.nl_geom)),
        ("unsymm", bool2text(solver_options.unsymm)),
        ("inc", step.total_incr),
    ]
    data = f"{_opt(step.init_incr)}, {_opt(step.total_time)}, {_opt(step.min_incr)}, {_opt(step.max_incr)}"
    return render_keyword("Step", step_params) + render_block("Static", static_params, [data])


def eigenfrequency_str(step: StepEigen):
    # The step's own name: this wrote every frequency step as "eig".
    frequency_params = [
        ("eigensolver", "Lanczos"),
        ("sim", "NO"),
        ("acoustic coupling", "on"),
        ("normalization", "displacement"),
    ]
    return render_keyword(
        "Step", [("name", step.name), ("nlgeom", "NO"), ("perturbation", None)], (), _step_banner(step)
    ) + render_keyword("Frequency", frequency_params, [f"{step.num_eigen_modes}, , , , ,"])


def _step_banner(step: _step_types) -> list[str]:
    return ["-" * 64, "", f"STEP: {step.name}", ""]


def complex_eig_str(step: StepEigenComplex):
    unsymm = bool2text(step.options.ABAQUS.unsymm)
    step_params = [("name", step.name), ("nlgeom", "NO"), ("perturbation", None), ("unsymm", unsymm)]
    return render_keyword("Step", step_params, (), _step_banner(step)) + render_keyword(
        "Complex Frequency", [("friction damping", "NO")], [f"{step.num_eigen_modes}, , ,"]
    )


def steady_state_response_str(step: StepSteadyState) -> str:
    load = step.unit_load
    # The DOF's POSITION: this took the entry's value, so a unit load [None, None, 1, ...] (DOF 3)
    # was written on DOF 1.
    directions = [i + 1 for i, dof in enumerate(load.dof) if dof]  # None or 0: no component there
    if len(directions) != 1:
        raise ValueError("Steady state analysis supports only a Unit load in a single degree of freedom")

    direction = directions[0]
    magnitude = load.magnitude * load.dof[direction - 1]
    set_ref = get_instance_name(load.fem_set, True)
    # The step's own name (this wrote "<name>_<fmin>_<fmax>Hz"); the range as the ONE data line the
    # Keywords Guide gives INTERVAL=RANGE -- lower, upper, number of points -- where this wrote a
    # hundred single frequencies rounded to 3 decimals; and the unit load against its set and under
    # its name. All of it reads back.
    return (
        render_keyword("STEP", [("NAME", step.name)], (), ["-" * 64], sep=",")
        + render_keyword(
            "STEADY STATE DYNAMICS",
            [("DIRECT", None), ("INTERVAL", "RANGE")],
            [f" {format_number(step.fmin)}, {format_number(step.fmax)}, 100"],
        )
        + render_keyword("GLOBAL DAMPING", [("ALPHA", format_number(step.alpha)), ("BETA", format_number(step.beta))])
        + render_keyword("LOAD CASE", [("NAME", "LC1")], (), [""])
        + render_keyword(
            "CLOAD",
            [("OP", "NEW")],
            [f" {set_ref}, {direction}, {format_number(magnitude)}"],
            [f"Name: {load.name}   Type: Concentrated force"],
        )
        + render_block("END LOAD CASE")
    )
