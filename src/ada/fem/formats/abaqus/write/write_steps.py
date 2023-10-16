from typing import TYPE_CHECKING, Union

import numpy as np

from ada.core.utils import bool2text
from ada.fem.steps import (
    Step,
    StepEigen,
    StepEigenComplex,
    StepExplicit,
    StepImplicitStatic,
    StepSteadyState,
)

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
    step_str_writer = step_map.get(step.type, None)
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
        if type(step) == StepExplicit:
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


def restart_request_str(step: _step_types):
    solver_options = step.options.ABAQUS
    if solver_options.restart_int is None:
        return "** No Restart Requests"
    return f"*Restart, write, frequency={solver_options.restart_int}"


def dynamic_implicit_str(step: StepImplicitStatic):
    return f"""*Step, name={step.name}, nlgeom={bool2text(step.nl_geom)}, inc={step.total_incr}
*Dynamic,application={step.dyn_type}, INITIAL={bool2text(step.options.ABAQUS.init_accel_calc)}
{step.init_incr},{step.total_time},{step.min_incr}, {step.max_incr}"""


def explicit_str(step: StepExplicit):
    return f"""*Step, name={step.name}, nlgeom={bool2text(step.nl_geom)}
*Dynamic, Explicit
, {step.total_time}
*Bulk Viscosity
0.06, 1.2"""


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
{step.init_incr}, {step.total_time}, {step.min_incr}, {step.max_incr}"""


def eigenfrequency_str(step: StepEigen):
    return f"""** ----------------------------------------------------------------
**
** STEP: eig
**
*Step, name=eig, nlgeom=NO, perturbation
*Frequency, eigensolver=Lanczos, sim=NO, acoustic coupling=on, normalization=displacement
{step.num_eigen_modes}, , , , ,
"""


def complex_eig_str(step: StepEigenComplex):
    unsymm = bool2text(step.options.ABAQUS.unsymm)
    return f"""** ----------------------------------------------------------------
**
** STEP: complex_eig
**
*Step, name={step.name}, nlgeom=NO, perturbation, unsymm={unsymm}
*Complex Frequency, friction damping=NO
{step.num_eigen_modes}, , ,
"""


def steady_state_response_str(step: StepSteadyState) -> str:
    load = step.unit_load
    directions = [dof for dof in load.dof if dof is not None]
    if len(directions) != 1:
        raise ValueError("Steady state analysis supports only a Unit load in a single degree of freedom")

    direction = directions[0]
    magnitude = load.magnitude
    node_ref = get_instance_name(load.fem_set.members[0], True)

    return f"""** ----------------------------------------------------------------
*STEP,NAME={step.name}_{step.fmin}_{step.fmax}Hz
*STEADY STATE DYNAMICS, DIRECT, INTERVAL=RANGE
 {add_freq_range(step.fmin, step.fmax)}
*GLOBAL DAMPING, ALPHA={step.alpha} , BETA={step.beta}
**
*LOAD CASE, NAME=LC1
*CLOAD, OP=NEW
 {node_ref}, {direction}, {magnitude}
*END LOAD CASE"""


def add_freq_range(fmin: float, fmax: float, intervals: int = 100):
    """Return a multiline string of frequency range given by <fmin> and <fmax> at a specific interval."""
    freq_list = np.linspace(fmin, fmax, intervals)
    freq_str = ""
    for eig in freq_list:
        if eig == freq_list[-1]:
            freq_str += "{0:.3f},".format(eig)
        else:
            freq_str += "{0:.3f},\n".format(eig)

    return freq_str
