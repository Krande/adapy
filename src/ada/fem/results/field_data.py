from dataclasses import dataclass
from typing import ClassVar

import numpy as np


@dataclass
class FieldData:
    name: str
    step: int
    components: list[str]
    values: np.ndarray


@dataclass
class NodalFieldData(FieldData):
    COLS: ClassVar[list[str]] = ["node_label"]


@dataclass
class ElementFieldData(FieldData):
    """Values from element integration points"""

    COLS: ClassVar[list[str]] = ["elem_label", "sec_num", "node_label"]


class LineSectionIntegrationPoints:
    ISection: dict = {1: "bottom left", 2: "bottom right", 3: "top left", 4: "top right"}
