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

    def get_values_only(self):
        num_cols = len(self.COLS)
        num_comp = len(self.components)

        if num_comp == 0:
            return self.values[:, -1]

        return np.array([self.values[:, i] for i in range(num_cols, num_comp)])


@dataclass
class ElementFieldData(FieldData):
    """Values from element integration points"""

    COLS: ClassVar[list[str]] = ["elem_label", "sec_num", "node_label"]

    def get_values_only(self):
        num_cols = len(self.COLS)
        num_comp = len(self.components)
        _ = np.unique(self.values[:, 0], return_index=True)

        if num_comp == 0:
            values = self.values[:, -1]
        else:
            values = np.array([self.values[:, i] for i in range(num_cols, num_cols + num_comp)])

        return values


class LineSectionIntegrationPoints:
    ISection: dict = {1: "bottom left", 2: "bottom right", 3: "top left", 4: "top right"}
