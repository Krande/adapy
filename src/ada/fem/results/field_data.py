from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

import numpy as np

if TYPE_CHECKING:
    from .common import Mesh


@dataclass
class FieldData:
    name: str
    step: int
    components: list[str]
    values: np.ndarray

    _mesh: Mesh = None


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

    def get_by_element_label(self):
        unique_elements, row_ids = np.unique(self.values[:, 0], return_index=True)
        num_comp = max(len(self.components), 1)

        res_array = np.zeros((len(unique_elements), num_comp))
        for i, el_id in enumerate(unique_elements):
            # res = self._mesh.get_elem_by_id(el_id)
            start = row_ids[i]
            end = row_ids[i + 1]
            res_array[i] = self.values[start:end, 2:]
        print("sd")

    def get_values_only(self):
        num_cols = len(self.COLS)
        num_comp = len(self.components)
        _ = self.get_by_element_label()

        if num_comp == 0:
            values = self.values[:, -1]
        else:
            values = np.array([self.values[:, i] for i in range(num_cols, num_cols + num_comp)])

        return values


class LineSectionIntegrationPoints:
    ISection: dict = {1: "bottom left", 2: "bottom right", 3: "top left", 4: "top right"}
