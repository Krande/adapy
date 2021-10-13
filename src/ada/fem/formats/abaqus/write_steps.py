from typing import Union

import numpy as np

from ada.core.utils import bool2text
from ada.fem.steps import (
    Step,
    StepEigen,
    StepEigenComplex,
    StepExplicit,
    StepImplicit,
    StepSteadyState,
)

from .templates import step_inp_str

_step_types = Union[StepEigen, StepExplicit, StepImplicit, StepSteadyState, StepEigenComplex]


class AbaStep:
    def __init__(self, step: _step_types):
        self.step = step
        if step.solver_options is None:
            from .solver import SolverOptionsAbaqus

            step.solver_options = SolverOptionsAbaqus()

    @property
    def _hist_output_str(self):
        from .writer import hist_output_str

        return (
            "\n".join([hist_output_str(hs) for hs in self.step.hist_outputs])
            if len(self.step.hist_outputs) > 0
            else "**"
        )

    @property
    def _field_output_str(self):
        from .writer import field_output_str

        return (
            "\n".join([field_output_str(fs) for fs in self.step.field_outputs])
            if len(self.step.field_outputs) > 0
            else "**"
        )

    @property
    def interactions_str(self):
        from .writer import interaction_str

        if len(self.step.interactions) == 0:
            return "** No Interactions"
        return "\n".join([interaction_str(interact, self) for interact in self.step.interactions.values()])

    @property
    def bc_str(self):
        from .writer import bc_str

        if len(self.step.bcs) == 0:
            return "** No BCs"

        bcstr = ""
        for bcid, bc_ in self.step.bcs.items():
            bcstr += "\n" if "\n" not in bcstr[-2:] != "" else ""
            bcstr += bc_str(bc_, self)

        return bcstr

    @property
    def load_str(self):
        from .write_loads import load_str

        if len(self.step.loads) == 0:
            return "** No Loads"

        return "\n".join([load_str(load) for load in self.step.loads])

    @property
    def restart_request_str(self):
        if self.step.solver_options.restart_int is None:
            return "** No Restart Requests"
        return f"*Restart, write, frequency={self.step.solver_options.restart_int}"

    @property
    def str(self):
        if "aba_inp" in self.step.metadata.keys():
            return self.step.metadata["aba_inp"]

        app_str = self.step.metadata["append"] if "append" in self.step.metadata.keys() else "**"

        st = Step.TYPES
        step_map = {
            st.STATIC: static_step_str,
            st.DYNAMIC: dynamic_implicit_str,
            st.EXPLICIT: explicit_str,
            st.EIGEN: eigenfrequency_str,
            st.STEADY_STATE: steady_state_response_str,
            st.COMPLEX_EIG: complex_eig_str,
        }
        step_str_writer = step_map.get(self.step.type, None)
        if step_str_writer is None:
            raise ValueError(f"Unrecognized step type {self.step.type}.")
        step_input_str = step_str_writer(self.step)

        return step_inp_str.format(
            name=self.step.name,
            step_input=step_input_str,
            bcs_str=self.bc_str,
            load_str=self.load_str,
            int_str=self.interactions_str,
            restart_request_str=self.restart_request_str,
            hist_output_str=self._hist_output_str,
            field_output_str=self._field_output_str,
            app_str=app_str,
        )


def dynamic_implicit_str(step: StepImplicit):
    return f"""*Step, name={step.name}, nlgeom={bool2text(step.nl_geom)}, inc={step.total_incr}
*Dynamic,application={step.dyn_type}, INITIAL={bool2text(step.solver_options.init_accel_calc)}
{step.init_incr},{step.total_time},{step.min_incr}, {step.max_incr}"""


def explicit_str(step: StepExplicit):
    return f"""*Step, name={step.name}, nlgeom={bool2text(step.nl_geom)}
*Dynamic, Explicit
, {step.total_time}
*Bulk Viscosity
0.06, 1.2"""


def static_step_str(step: StepImplicit):
    stabilize_str = ""
    stabilize = step.solver_options.stabilize

    if stabilize is not None:
        stabilize_str = ", " + stabilize.to_input_str()

    line1 = (
        f"*Step, name={step.name}, nlgeom={bool2text(step.nl_geom)}, "
        f"unsymm={bool2text(step.solver_options.unsymm)}, inc={step.total_incr}"
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
    unsymm = bool2text(step.solver_options.unsymm)
    return f"""** ----------------------------------------------------------------
**
** STEP: complex_eig
**
*Step, name={step.name}, nlgeom=NO, perturbation, unsymm={unsymm}
*Complex Frequency, friction damping=NO
{step.num_eigen_modes}, , ,
"""


def steady_state_response_str(step: StepSteadyState) -> str:
    from .writer import get_instance_name

    load = step.unit_load
    directions = [dof for dof in load.dof if dof is not None]
    if len(directions) != 1:
        raise ValueError("Steady state analysis supports only a Unit load in a single degree of freedom")

    direction = directions[0]
    magnitude = load.magnitude
    node_ref = get_instance_name(load.fem_set.members[0], step)

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
