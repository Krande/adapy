from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Union

import numpy as np


@dataclass
class CustomJson:
    name: str
    created: str
    project: str
    world: List[MergedPart]
    meta: dict

    def to_dict(self):
        return {
            "name": self.name,
            "created": self.created,
            "project": self.project,
            "world": [x.to_dict() for x in self.world],
            "meta": self.meta,
        }


@dataclass
class MergedPart:
    name: str
    rawdata: bool
    id_map: Dict[str, PolyModel]
    guiparam: Union[None, dict] = None

    def to_dict(self):
        return {
            "name": self.name,
            "rawdata": self.rawdata,
            "guiParam": self.guiparam,
            "id_map": {key: value.to_dict() for key, value in self.id_map.items()},
        }


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
    translation: np.ndarray = None

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
            translation=self.translation.astype(float).tolist(),
        )

    def __add__(self, other: PolyModel):
        pos_len = int(len(self.position) / 3)
        new_index = other.index + pos_len
        ma = int((len(other.index) + len(self.index))) - 1
        mi = int(len(self.index))

        self.index = np.concatenate([self.index, new_index])
        self.position = np.concatenate([self.position, other.position])

        if self.color is None:
            self.color = other.color
        else:
            if other.color[-1] == 1.0 and self.color[-1] != 1.0:
                logging.warning("Will merge colors with different opacity.")
                self.color[-1] = 1.0

        if self.translation is None and other.translation is not None:
            self.translation = other.translation

        if self.normal is None or other.normal is None:
            self.normal = None
        else:
            self.normal = np.concatenate([self.normal, other.normal])

        self.id_sequence[other.guid] = (mi, ma)
        return self
