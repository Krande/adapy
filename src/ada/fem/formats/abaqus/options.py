from .elem_formulations import AbaqusDefaultElemTypes


class AbaqusInpFormat:
    def __init__(self):
        self.underline_prefix_is_internal = True


class AbaqusOptions:
    def __init__(self):
        self.default_elements = AbaqusDefaultElemTypes()
        self.inp_format = AbaqusInpFormat()
