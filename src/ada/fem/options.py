from .formats.abaqus.options import AbaqusOptions


class FemOptions:
    def __init__(self):
        self.ABAQUS = AbaqusOptions()
        self.CALCULIX = AbaqusOptions()
