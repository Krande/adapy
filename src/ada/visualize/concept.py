from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class PolyModel:
    guid: str
    index: np.ndarray
    position: np.ndarray
    normal: np.ndarray
    color: list
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
        return dict(
            index=self.index.astype(int).tolist(),
            position=self.position.astype(float).tolist(),
            normal=self.normal.astype(float).tolist(),
            color=self.color,
            vertexColor=self.vertexColor,
            instances=self.instances,
            id_sequence=self.id_sequence,
        )

    def __add__(self, other: PolyModel):
        pos_len = int(len(self.position) / 3)
        new_index = other.index + pos_len
        mi, ma = int(len(self.index)), int((len(other.index) + len(self.index))) - 1

        self.index = np.concatenate([self.index, new_index])
        self.position = np.concatenate([self.position, other.position])
        self.normal = np.concatenate([self.normal, other.normal])
        self.id_sequence[other.guid] = (mi, ma)
        return self
