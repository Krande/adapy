from dataclasses import dataclass

from .elem_formulations import AbaqusDefaultElemTypes


@dataclass
class AbaqusInpFormat:
    underline_prefix_is_internal = True


@dataclass
class AbaqusOptions:
    default_elements = AbaqusDefaultElemTypes()
    inp_format: AbaqusInpFormat = AbaqusInpFormat()
