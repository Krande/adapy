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

from ..grammar import format_number
from .helper_utils import get_instance_name
from .templates import step_inp_str

if TYPE_CHECKING:
    from ada import FEM

_step_types = Union[StepEigen, StepExplicit, StepImplicitStatic, StepSteadyState, StepEigenComplex]


def main_step_inp_str(step: _step_types) -> str:
    return f"""*INCLUDE,INPUT=core_input_files\\step_{step.name}.inp"""


def write_step(step_in: _step_types, analysis_dir):
    step_str = abaqus_step_str(step_in)
    with open(analysis_dir / "core_input_files" / f"step_{step_in.name}.inp", "w") as d:
        d.write(step_str)
        if "*End Step" not in step_str:
            d.write("*End Step\n")


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


def constraint_control(fem: "FEM"):
    constraint_ctrl_on = True
    for step in fem.steps:
        if type(step) is StepExplicit:
            constraint_ctrl_on = False
    return "**" if constraint_ctrl_on is False else "*constraint controls, print=yes"


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
    from .write_bc import bc_str

    if len(step.bcs) == 0:
        return "** No BCs"

    bcstr = ""
    for bcid, bc_ in step.bcs.items():
        bcstr += "\n" if "\n" not in bcstr[-2:] != "" else ""
        bcstr += bc_str(bc_, True)

    return bcstr


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
    return f"*Restart, write, frequency={solver_options.restart_int}"


def dynamic_implicit_str(step: StepImplicitStatic):
    return f"""*Step, name={step.name}, nlgeom={bool2text(step.nl_geom)}, inc={step.total_incr}
*Dynamic,application={step.dyn_type}, INITIAL={bool2text(step.options.ABAQUS.init_accel_calc)}
{_opt(step.init_incr)},{_opt(step.total_time)},{_opt(step.min_incr)}, {_opt(step.max_incr)}"""


def explicit_str(step: StepExplicit):
    # No *Bulk Viscosity: it was hard-coded to 0.06, 1.2 -- Abaqus's own defaults, so writing it
    # changed nothing, and no step attribute stood behind it for a reader to give back.
    return f"""*Step, name={step.name}, nlgeom={bool2text(step.nl_geom)}
*Dynamic, Explicit
, {_opt(step.total_time)}"""


def static_step_str(step: StepImplicitStatic):
    stabilize_str = ""
    solver_options = step.options.ABAQUS
    stabilize = solver_options.stabilize

    if stabilize is not None:
        stabilize_str = ", " + stabilize.to_input_str()

    line1 = (
        f"*Step, name={step.name}, nlgeom={bool2text(step.nl_geom)}, "
        f"unsymm={bool2text(solver_options.unsymm)}, inc={step.total_incr}"
    )

    return f"""{line1}
*Static{stabilize_str}
{_opt(step.init_incr)}, {_opt(step.total_time)}, {_opt(step.min_incr)}, {_opt(step.max_incr)}"""


def eigenfrequency_str(step: StepEigen):
    # The step's own name: this wrote every frequency step as "eig".
    return f"""** ----------------------------------------------------------------
**
** STEP: {step.name}
**
*Step, name={step.name}, nlgeom=NO, perturbation
*Frequency, eigensolver=Lanczos, sim=NO, acoustic coupling=on, normalization=displacement
{step.num_eigen_modes}, , , , ,
"""


def complex_eig_str(step: StepEigenComplex):
    unsymm = bool2text(step.options.ABAQUS.unsymm)
    return f"""** ----------------------------------------------------------------
**
** STEP: {step.name}
**
*Step, name={step.name}, nlgeom=NO, perturbation, unsymm={unsymm}
*Complex Frequency, friction damping=NO
{step.num_eigen_modes}, , ,
"""


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
    return f"""** ----------------------------------------------------------------
*STEP,NAME={step.name}
*STEADY STATE DYNAMICS, DIRECT, INTERVAL=RANGE
 {format_number(step.fmin)}, {format_number(step.fmax)}, 100
*GLOBAL DAMPING, ALPHA={format_number(step.alpha)} , BETA={format_number(step.beta)}
**
*LOAD CASE, NAME=LC1
** Name: {load.name}   Type: Concentrated force
*CLOAD, OP=NEW
 {set_ref}, {direction}, {format_number(magnitude)}
*END LOAD CASE"""
