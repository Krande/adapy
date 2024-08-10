from .formats.abaqus.options import AbaqusOptions
from .formats.calculix.options import CalculixOptions
from .formats.code_aster.options import CodeAsterOptions


class FemOptions:
    def __init__(self):
        self.ABAQUS = AbaqusOptions()
        self.CALCULIX = CalculixOptions()
        self.CODE_ASTER = CodeAsterOptions()
