from typing import List

from .common import FemBase
from .constraints import Bc
from .interactions import Interaction
from .loads import Load
from .outputs import FieldOutput, HistOutput


class StepTypes:
    STATIC = "static"
    EIGEN = "eigenfrequency"
    COMPLEX_EIG = "complex_eig"
    RESP = "response_analysis"
    DYNAMIC = "dynamic"
    EXPLICIT = "explicit"
    all = [STATIC, EIGEN, RESP, DYNAMIC, COMPLEX_EIG, EXPLICIT]


class DynStepType:
    QUASI_STATIC = "QUASI-STATIC"
    TRANSIENT_FIDELITY = "TRANSIENT FIDELITY"
    all = [QUASI_STATIC, TRANSIENT_FIDELITY]


class Step(FemBase):
    """
    A FEM analysis step object

    :param name: Name of step
    :param step_type: Step type: | 'static' | 'eigenfrequency' |  'response_analysis' | 'dynamic' | 'complex_eig' |
    :param nl_geom: Include or ignore the nonlinear effects of large deformations and displacements (default=False)
    :param total_incr: Maximum number of allowed increments
    :param init_incr: Initial increment
    :param total_time: Total step time
    :param min_incr: Minimum allowable increment size
    :param max_incr: Maximum allowable increment size
    :param unsymm: Unsymmetric Matrix storage (default=False)
    :param stabilize: Default=None.
    :param dyn_type: Dynamic analysis type 'TRANSIENT FIDELITY' | 'QUASI-STATIC'
    :param init_accel_calc: Initial acceleration calculation
    :param eigenmodes: Number of requested Eigenmodes
    :param alpha: Rayleigh Damping for use in Steady State analysis
    :param beta: Rayleigh Damping for use in Steady State analysis
    :param nodeid: Node ID for use in Steady State analysis
    :param fmin: Minimum frequency for use in Steady State analysis
    :param fmax: Maximum frequency for use in Steady State analysis
    """

    TYPES = StepTypes
    TYPES_DYNAMIC = DynStepType

    default_hist = HistOutput("default_hist", None, "energy", HistOutput.default_hist)
    default_field = FieldOutput("default_fields", int_type="FREQUENCY", int_value=1)

    def __init__(
        self,
        name,
        step_type,
        total_time=None,
        nl_geom=False,
        total_incr=1000,
        init_incr=100.0,
        min_incr=1e-8,
        max_incr=100.0,
        unsymm=False,
        stabilize=None,
        dyn_type="QUASI-STATIC",
        init_accel_calc=True,
        eigenmodes=20,
        alpha=0.1,
        beta=10,
        nodeid=None,
        fmin=0,
        restart_int=None,
        fmax=10,
        visco=None,
        metadata=None,
        parent=None,
    ):
        super().__init__(name, metadata, parent)
        if step_type not in StepTypes.all:
            raise ValueError(f'Step type "{step_type}" is currently not supported')
        if total_time is not None:
            if init_incr > total_time and step_type != StepTypes.EXPLICIT and nl_geom is True:
                raise ValueError(f"Initial increment ({init_incr}) must be smaller than total time ({total_time})")
        else:
            total_time = init_incr
        if dyn_type not in DynStepType.all:
            raise ValueError(f'Dynamic input type "{dyn_type}" is not supported')

        self._restart_int = restart_int
        self._step_type = step_type
        self._nl_geom = nl_geom
        self._total_incr = total_incr
        self._init_incr = init_incr
        self._total_time = total_time
        self._min_incr = min_incr
        self._max_incr = max_incr
        self._unsymm = unsymm
        self._stabilize = stabilize
        self._dyn_type = dyn_type
        self._init_accel_calc = init_accel_calc
        self._eigenmodes = eigenmodes
        self._alpha = alpha
        self._beta = beta
        self._nodeid = nodeid
        self._fmin = fmin
        self._fmax = fmax
        self._visco = visco

        # Not-initialized parameters
        self._bcs = dict()
        self._loads = []
        self._interactions = dict()
        self._hist_outputs = [self.default_hist]
        self._field_outputs = [self.default_field]

    def add_load(self, load: Load):
        self._loads.append(load)

    def add_bc(self, bc: Bc):
        bc.parent = self
        self._bcs[bc.name] = bc

        if bc.fem_set not in self.parent.sets and bc.fem_set.parent is None:
            self.parent.sets.add(bc.fem_set)

    def add_history_output(self, hist_output: HistOutput):
        hist_output.parent = self
        self._hist_outputs.append(hist_output)

    def add_field_output(self, field_output: FieldOutput):
        field_output.parent = self
        self._field_outputs.append(field_output)

    def add_interaction(self, interaction: Interaction):
        interaction.parent = self
        self._interactions[interaction.name] = interaction

    @property
    def type(self):
        return self._step_type

    @property
    def nl_geom(self):
        return self._nl_geom

    @property
    def total_incr(self):
        return self._total_incr

    @property
    def init_incr(self):
        return self._init_incr

    @property
    def total_time(self):
        return self._total_time

    @property
    def min_incr(self):
        return self._min_incr

    @property
    def max_incr(self):
        return self._max_incr

    @property
    def unsymm(self):
        return self._unsymm

    @property
    def stabilize(self):
        return self._stabilize

    @property
    def dyn_type(self):
        return self._dyn_type

    @property
    def init_accel_calc(self):
        return self._init_accel_calc

    @property
    def eigenmodes(self):
        return self._eigenmodes

    @property
    def alpha(self):
        return self._alpha

    @property
    def beta(self):
        return self._beta

    @property
    def nodeid(self):
        return self._nodeid

    @property
    def fmin(self):
        return self._fmin

    @property
    def fmax(self):
        return self._fmax

    @property
    def visco(self):
        return self._visco

    @property
    def interactions(self):
        return self._interactions

    @property
    def bcs(self):
        return self._bcs

    @property
    def restart_int(self):
        """Restart request intervals"""
        return self._restart_int

    @property
    def loads(self) -> List[Load]:
        return self._loads

    @property
    def field_outputs(self) -> List[FieldOutput]:
        return self._field_outputs

    @property
    def hist_outputs(self) -> List[HistOutput]:
        return self._hist_outputs

    def __repr__(self):
        return f"Step({self.name}, type={self.type}, nl_geom={self.nl_geom})"
