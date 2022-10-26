from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, ClassVar

import numpy as np

if TYPE_CHECKING:
    pass


@dataclass
class FieldData:
    name: str
    step: int
    components: list[str]
    values: np.ndarray


@dataclass
class NodalFieldData(FieldData):
    COLS: ClassVar[list[str]] = ["node_label"]

    def get_all_values(self):
        num_cols = len(self.COLS)
        num_comp = len(self.components)
        cr = list(range(num_cols, num_cols + num_comp + 1)) if num_comp != 0 else -1

        if num_comp == 0:
            return self.values[:, -1]

        result = self.values[:, cr[0] : cr[-1]]
        return result


class FieldPosition(Enum):
    NODAL = "nodal"
    INT = "integration_point"


@dataclass
class ElementFieldData(FieldData):
    """Values from element integration points"""

    field_pos: FieldPosition = FieldPosition.NODAL
    COLS: ClassVar[list[str]] = ["elem_label", "sec_num"]
    int_positions: list[tuple] = None

    def get_by_element_label(self):
        unique_elements, row_ids = np.unique(self.values[:, 0], return_index=True)
        num_comp = max(len(self.components), 1)

        res_array = np.zeros((len(unique_elements), num_comp))
        for i, el_id in enumerate(unique_elements):
            # res = self._mesh.get_elem_by_id(el_id)
            start = row_ids[i]
            end = row_ids[i + 1]
            res_array[i] = self.values[start:end, 2:]

    def _get_field_nodal(self, cr: int):
        nodes = self.values[:, 2]
        unique_nodes = np.unique(nodes)
        num_unique = len(unique_nodes)

        result_data = np.arange(0, num_unique)

        for i, unique_nodal in enumerate(unique_nodes):
            instances = np.where(nodes == unique_nodal)
            if cr != -1:
                field_data = self.values[instances, cr[0] : cr[-1]].ravel()
            else:
                field_data = self.values[instances, cr].ravel()
            averaged_data = sum(field_data) / len(field_data)
            result_data[i] = averaged_data

        return result_data

    def _get_field_int(self, cr) -> np.ndarray | dict[str, np.ndarray]:
        elements = self.values[:, 0]
        unique_element_ids = np.unique(elements)
        num_unique = len(unique_element_ids)

        if cr != -1:
            num_int_points = int(len(self.values) / num_unique)
            result_dict = {}
            for j, x in enumerate(self.components, start=cr[0]):
                result_data = np.zeros((num_unique, num_int_points))
                for i, unique_elements in enumerate(unique_element_ids):
                    instances = np.where(elements == unique_elements)
                    comp = self.values[instances, j]
                    result_data[i] = comp

                result_dict[x] = [result_data]

            return result_dict
        else:
            num_int_points = int(len(self.values) / num_unique) * len(self.components)
            result_data = np.zeros((num_unique, num_int_points))
            for i, unique_elements in enumerate(unique_element_ids):
                instances = np.where(elements == unique_elements)
                field_data = self.values[instances, cr].ravel()
                result_data[i] = field_data

                return result_data

    def get_all_values(self):
        num_cols = len(self.COLS)
        num_comp = len(self.components)
        cr = list(range(num_cols, num_cols + num_comp + 1)) if num_comp != 0 else -1

        if self.field_pos == self.field_pos.NODAL:
            return self._get_field_nodal(cr)
        else:
            return self._get_field_int(cr)


class LineSectionIntegrationPoints:
    ISection: dict = {1: "bottom left", 2: "bottom right", 3: "top left", 4: "top right"}
