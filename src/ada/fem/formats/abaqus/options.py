from dataclasses import dataclass

from .solver import SolverOptionsAbaqus


@dataclass
class AbaqusInpFormat:
    underline_prefix_is_internal = True


@dataclass
class AbaqusOptions:
    solver: SolverOptionsAbaqus = SolverOptionsAbaqus()
    inp_format: AbaqusInpFormat = AbaqusInpFormat()
