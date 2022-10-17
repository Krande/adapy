from dataclasses import dataclass

import numpy as np


@dataclass
class FieldData:
    name: str
    step: int
    components: list[str]


@dataclass
class NodalFieldData(FieldData):
    values: np.ndarray


@dataclass
class ElementFieldData(FieldData):
    """Values from element integration points"""

    el_label: int
    no_label: int
    values: dict[int, np.ndarray]


class LineSectionIntegrationPoints:
    ISection: dict = {1: "bottom left", 2: "bottom right", 3: "top left", 4: "top right"}
