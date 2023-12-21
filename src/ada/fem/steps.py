from __future__ import annotations

from typing import TYPE_CHECKING, Dict, List, Union

from ada.config import logger

from .common import FemBase
from .constraints import Bc
from .interactions import Interaction
from .loads import Load, LoadCase, LoadGravity, LoadPressure
from .outputs import FieldOutput, HistOutput

if TYPE_CHECKING:
    from ada import FEM


class _StepTypes:
    STATIC = "static"
    EIGEN = "eigenfrequency"
    COMPLEX_EIG = "complex_eig"
    STEADY_STATE = "steady_state"
    DYNAMIC = "dynamic"
    EXPLICIT = "explicit"

    all = [STATIC, EIGEN, STEADY_STATE, DYNAMIC, COMPLEX_EIG, EXPLICIT]


class _DynStepType:
    QUASI_STATIC = "QUASI-STATIC"
    TRANSIENT_FIDELITY = "TRANSIENT FIDELITY"
    MODERATE_DISSIPATION = "MODERATE DISSIPATION"
    all = [QUASI_STATIC, TRANSIENT_FIDELITY, MODERATE_DISSIPATION]


class StepSolverOptions:
    """A class for FE solver specific options"""

    def __init__(self):
        self._ABAQUS = None
        self._CODE_ASTER = None
        self._CALCULIX = None

    @property
    def CODE_ASTER(self):
        return NotImplementedError()

    @property
    def CALCULIX(self):
        return NotImplementedError()

    @property
    def ABAQUS(self):
        if self._ABAQUS is None:
            from .formats.abaqus.solver import AbaqusStepOptions

            self._ABAQUS = AbaqusStepOptions()
        return self._ABAQUS


class Step(FemBase):
    """
    A FEM analysis step object

    :param name: Name of step
    :param step_type: Step type: | 'static' | 'eigenfrequency' |  'response_analysis' | 'dynamic' | 'complex_eig' |
    :param nl_geom: Include or ignore the nonlinear effects of large deformations and displacements (default=False)
    """

    TYPES = _StepTypes

    def __init__(
        self,
        name,
        step_type,
        nl_geom=False,
        total_time=None,
        solver_options: StepSolverOptions = StepSolverOptions(),
        use_default_outputs=True,
        metadata=None,
        parent: "FEM" = None,
    ):
        super().__init__(name, metadata, parent)
        if step_type not in _StepTypes.all:
            raise ValueError(f'Step type "{step_type}" is currently not supported')

        self._total_time = total_time
        self._step_type = step_type
        self._nl_geom = nl_geom
        self._solver_options = solver_options
        self._bcs: Dict[str, Bc] = dict()
        self._loads: List[Load] = []
        self._load_cases = dict()
        self._interactions = dict()
        self._hist_outputs = []
        self._field_outputs = []

        if use_default_outputs:
            hist, field = self.get_default_output_variables()
            self._hist_outputs += [hist]
            self._field_outputs += [field]

    def get_default_output_variables(self):
        from ada.fem.outputs import defaults

        return defaults()

    def add_load(self, load: Union[Load, LoadPressure, LoadGravity]):
        if isinstance(load, LoadPressure):
            if load.surface.parent is None:
                self.parent.add_surface(load.surface)
        load.parent = self
        self._loads.append(load)

    def add_loadcase(self, load_case: LoadCase):
        for load in load_case.loads:
            if load not in self.loads:
                self.loads.append(load)
                load.parent = self
        load_case.parent = self
        self._load_cases[load_case.name] = load_case

    def add_bc(self, bc: Bc):
        bc.parent = self
        self._bcs[bc.name] = bc
        if bc.fem_set.parent is None and bc.fem_set not in self.parent.sets:
            self.parent.sets.add(bc.fem_set)
        if bc.amplitude is not None and bc.amplitude.parent is None:
            self.parent.add_amplitude(bc.amplitude)

    def add_history_output(self, hist_output: HistOutput):
        hist_output.parent = self
        if hist_output.fem_set.parent is None and self.parent is not None:
            self.parent.add_set(hist_output.fem_set)
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
    def options(self) -> StepSolverOptions:
        return self._solver_options

    @options.setter
    def options(self, value):
        self._solver_options = value

    @property
    def total_time(self):
        return self._total_time

    @property
    def interactions(self) -> dict[str, Interaction]:
        return self._interactions

    @property
    def bcs(self) -> dict[str, Bc]:
        return self._bcs

    @property
    def loads(self) -> list[Load]:
        return self._loads

    @property
    def load_cases(self) -> dict[str, LoadCase]:
        return self._load_cases

    @property
    def field_outputs(self) -> list[FieldOutput]:
        return self._field_outputs

    @property
    def hist_outputs(self) -> list[HistOutput]:
        return self._hist_outputs

    def __repr__(self):
        return f"{self.__class__.__name__}({self.name}, type={self.type}, nl_geom={self.nl_geom})"


class StepImplicitStatic(Step):
    def __init__(
        self,
        name,
        implicit_type=Step.TYPES.STATIC,
        nl_geom=False,
        total_time=100.0,
        total_incr=1000,
        init_incr=100.0,
        min_incr=1e-8,
        max_incr=100.0,
        **kwargs,
    ):
        """
        :param total_incr: Maximum number of allowed increments
        :param init_incr: Initial increment
        :param total_time: Total step time
        :param min_incr: Minimum allowable increment size
        :param max_incr: Maximum allowable increment size
        :param dyn_type: Dynamic analysis type 'TRANSIENT FIDELITY' | 'QUASI-STATIC'
        """
        if total_time is not None:
            if init_incr > total_time and nl_geom is True:
                logger.warning(
                    f"Initial increment > Total time ({init_incr} > {total_time}). "
                    "Adjusted initial increment equal to total time"
                )
                init_incr = total_time
        else:
            total_time = init_incr

        super(StepImplicitStatic, self).__init__(name, implicit_type, total_time=total_time, nl_geom=nl_geom, **kwargs)

        self._total_incr = total_incr
        self._init_incr = init_incr
        self._min_incr = min_incr
        self._max_incr = max_incr

    @property
    def total_incr(self):
        return self._total_incr

    @property
    def init_incr(self):
        return self._init_incr

    @property
    def min_incr(self):
        return self._min_incr

    @property
    def max_incr(self):
        return self._max_incr


class StepImplicitDynamic(StepImplicitStatic):
    TYPES_DYNAMIC = _DynStepType

    def __init__(
        self,
        name,
        dyn_type=TYPES_DYNAMIC.QUASI_STATIC,
        nl_geom=False,
        total_time=100.0,
        total_incr=1000,
        init_incr=100.0,
        min_incr=1e-8,
        max_incr=100.0,
        **kwargs,
    ):
        if dyn_type not in _DynStepType.all:
            raise ValueError(f'Dynamic input type "{dyn_type}" is not supported')

        self._dyn_type = dyn_type

        super().__init__(
            name=name,
            implicit_type=Step.TYPES.DYNAMIC,
            nl_geom=nl_geom,
            total_time=total_time,
            total_incr=total_incr,
            init_incr=init_incr,
            min_incr=min_incr,
            max_incr=max_incr,
            **kwargs,
        )

    @property
    def dyn_type(self):
        return self._dyn_type


class StepExplicit(Step):
    def __init__(self, name, **kwargs):
        super(StepExplicit, self).__init__(name, Step.TYPES.EXPLICIT, **kwargs)


class StepEigen(Step):
    def __init__(self, name, num_eigen_modes: int, field_el_outputs=None, **kwargs):
        super(StepEigen, self).__init__(name, Step.TYPES.EIGEN, **kwargs)
        self._num_eigen_modes = num_eigen_modes
        for field in self.field_outputs:
            field.element = [] if field_el_outputs is None else field_el_outputs

    @property
    def num_eigen_modes(self):
        """Number of requested Eigen modes"""
        return self._num_eigen_modes


class StepEigenComplex(StepEigen):
    def __init__(self, name, num_eigen_modes, friction_damping=False, **kwargs):
        super(StepEigenComplex, self).__init__(name, num_eigen_modes, **kwargs)
        self._friction_damping = friction_damping

    @property
    def friction_damping(self):
        return self._friction_damping


class StepSteadyState(Step):
    """
    :param alpha: Rayleigh Damping
    :param beta: Rayleigh Damping
    :param unit_load: Unit Load
    :param fmin: Minimum frequency
    :param fmax: Maximum frequency
    """

    def __init__(self, name, unit_load: Load, fmin, fmax, alpha=0.1, beta=10, **kwargs):
        super(StepSteadyState, self).__init__(name, Step.TYPES.STEADY_STATE, **kwargs)
        self._alpha = alpha
        self._beta = beta
        self._fmin = fmin
        self._fmax = fmax
        self._unit_load = unit_load

    @property
    def fmin(self):
        return self._fmin

    @property
    def fmax(self):
        return self._fmax

    @property
    def alpha(self):
        return self._alpha

    @property
    def beta(self):
        return self._beta

    @property
    def unit_load(self):
        return self._unit_load
