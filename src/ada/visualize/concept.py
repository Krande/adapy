from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

import numpy as np


@dataclass
class PolyModel:
    guid: str
    index: np.ndarray
    position: np.ndarray
    normal: Union[np.ndarray, None]
    color: list = None
    vertexColor: np.ndarray = None
    instances: np.ndarray = None
    id_sequence: dict = field(default_factory=dict)

    def __post_init__(self):
        pos_shape = np.shape(self.position)
        normal_shape = np.shape(self.normal)

        if len(pos_shape) > 1 and pos_shape[1] == 3:
            self.position = self.position.flatten().astype(float)
        if len(normal_shape) > 1 and normal_shape[1] == 3:
            self.normal = self.normal.flatten().astype(float)

    def to_dict(self):
        normal = self.normal.astype(float).tolist() if self.normal is not None else self.normal
        return dict(
            index=self.index.astype(int).tolist(),
            position=self.position.astype(float).tolist(),
            normal=normal,
            color=self.color,
            vertexColor=self.vertexColor,
            instances=self.instances,
            id_sequence=self.id_sequence,
        )

    def __add__(self, other: PolyModel):
        pos_len = int(len(self.position) / 3)
        new_index = other.index + pos_len
        ma = int((len(other.index) + len(self.index)))
        mi = int(len(self.index))

        self.index = np.concatenate([self.index, new_index])
        self.position = np.concatenate([self.position, other.position])

        if self.color is None:
            self.color = other.color

        if self.normal is None or other.normal is None:
            self.normal = None
        else:
            self.normal = np.concatenate([self.normal, other.normal])

        self.id_sequence[other.guid] = (mi, ma)
        return self
