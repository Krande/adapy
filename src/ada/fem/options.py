from dataclasses import dataclass

from .formats.abaqus.options import AbaqusOptions


@dataclass
class FemOptions:
    ABAQUS = AbaqusOptions()
