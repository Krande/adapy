from dataclasses import dataclass

from .elem_formulations import AbaqusDefaultElemTypes
from .solver import SolverOptionsAbaqus


@dataclass
class AbaqusInpFormat:
    underline_prefix_is_internal = True


@dataclass
class AbaqusOptions:
    solver: SolverOptionsAbaqus = SolverOptionsAbaqus()
    default_elements = AbaqusDefaultElemTypes()
    inp_format: AbaqusInpFormat = AbaqusInpFormat()
