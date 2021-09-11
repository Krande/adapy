import logging

import numpy as np

from ada.core.utils import bool2text
from ada.fem import Step

from .templates import step_inp_str


class AbaStep:
    def __init__(self, step: Step):
        self.step = step

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
        from .writer import load_str

        if len(self.step.loads) == 0:
            return "** No Loads"

        return "\n".join([load_str(load) for load in self.step.loads])

    @property
    def restart_request_str(self):
        if self.step.restart_int is None:
            return "** No Restart Requests"
        return f"*Restart, write, frequency={self.step.restart_int}"

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
            st.RESP: steady_state_response_str,
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


def dynamic_implicit_str(step: Step):
    return f"""*Step, name={step.name}, nlgeom={bool2text(step.nl_geom)}, inc={step.total_incr}
*Dynamic,application={step.dyn_type}, INITIAL={bool2text(step.init_accel_calc)}
{step.init_incr},{step.total_time},{step.min_incr}, {step.max_incr}"""


def explicit_str(step: Step):
    return f"""*Step, name={step.name}, nlgeom={bool2text(step.nl_geom)}
*Dynamic, Explicit
, {step.total_time}
*Bulk Viscosity
0.06, 1.2"""


def static_step_str(step: Step):
    static_str = ""
    stabilize = step.stabilize
    if stabilize is None:
        pass
    elif type(stabilize) is dict:
        stabkeys = list(stabilize.keys())
        if "energy" in stabkeys:
            static_str += ", stabilize={}, allsdtol={}".format(stabilize["energy"], stabilize["allsdtol"])
        elif "damping" in stabkeys:
            static_str += ", stabilize, factor={}, allsdtol={}".format(stabilize["damping"], stabilize["allsdtol"])
        elif "continue" in stabkeys:
            if stabilize["continue"] == "YES":
                static_str += ", stabilize, continue={}".format(stabilize["continue"])
        else:
            static_str += ", stabilize=0.0002, allsdtol=0.05"
            print(
                'Unrecognized stabilization type "{}". Will revert to energy stabilization "{}"'.format(
                    stabkeys[0], static_str
                )
            )
    elif stabilize is True:
        static_str += ", stabilize=0.0002, allsdtol=0.05"
    elif stabilize is False:
        pass
    else:
        static_str += ", stabilize=0.0002, allsdtol=0.05"
        logging.error(
            "Unrecognized stabilize input. Can be bool, dict or None. " 'Reverting to default stabilizing type "energy"'
        )
    line1 = (
        f"*Step, name={step.name}, nlgeom={bool2text(step.nl_geom)}, "
        f"unsymm={bool2text(step.unsymm)}, inc={step.total_incr}"
    )

    return f"""{line1}
*Static{static_str}
{step.init_incr}, {step.total_time}, {step.min_incr}, {step.max_incr}"""


def eigenfrequency_str(step: Step):
    return f"""** ----------------------------------------------------------------
**
** STEP: eig
**
*Step, name=eig, nlgeom=NO, perturbation
*Frequency, eigensolver=Lanczos, sim=NO, acoustic coupling=on, normalization=displacement
{step.eigenmodes}, , , , ,
"""


def complex_eig_str(step: Step):
    return f"""** ----------------------------------------------------------------
**
** STEP: complex_eig
**
*Step, name=complex_eig, nlgeom=NO, perturbation, unsymm=YES
*Complex Frequency, friction damping=NO
{step.eigenmodes}, , ,
"""


def steady_state_response_str(step: Step):
    if step.nodeid is None:
        raise ValueError("Please define a nodeid for the steady state load")

    return f"""** ----------------------------------------------------------------
*STEP,NAME=Response_Analysis_{step.fmin}_{step.fmax}Hz
*STEADY STATE DYNAMICS, DIRECT, INTERVAL=RANGE
{add_freq_range(step.fmin, step.fmax)}
*GLOBAL DAMPING, ALPHA={step.alpha} , BETA={step.beta}
**
*LOAD CASE, NAME=LC1
*CLOAD, OP=NEW
{step.nodeid},2, 1
*END LOAD CASE
**
*OUTPUT, FIELD, FREQUENCY=1
*NODE OUTPUT
U
**
*OUTPUT, HISTORY, FREQUENCY=1
*NODE OUTPUT, NSET=accel_data_set
UT, AT, TU, TA
**
"""


def add_freq_range(fmin, fmax, intervals=100):
    """
    Return a multiline string of frequency range given by <fmin> and <fmax> at a specific interval.

    :param intervals: Number of intervals for frequency range. Default is 100.
    :param fmin: Minimum frequency
    :param fmax: Maximum frequency
    :return:
    """
    freq_list = np.linspace(fmin, fmax, intervals)
    freq_str = ""
    for eig in freq_list:
        if eig == freq_list[-1]:
            print("last one")
            freq_str += "{0:.3f},".format(eig)
        else:
            freq_str += "{0:.3f},\n".format(eig)

    return freq_str
