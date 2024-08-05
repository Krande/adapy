from .formats.abaqus.options import AbaqusOptions
from .formats.code_aster.options import CodeAsterOptions


class FemOptions:
    def __init__(self):
        self.ABAQUS = AbaqusOptions()
        self.CALCULIX = AbaqusOptions()
        self.CODE_ASTER = CodeAsterOptions()
