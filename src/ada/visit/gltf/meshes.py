from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from ada.visit.colors import Color


class MeshType(Enum):
    # https://registry.khronos.org/glTF/specs/2.0/glTF-2.0.html#_mesh_primitive_mode
    POINTS = 0
    LINES = 1
    LINE_LOOP = 2
    LINE_STRIP = 3
    TRIANGLES = 4
    TRIANGLE_STRIP = 5
    TRIANGLE_FAN = 6

    @classmethod
    def from_int(cls, value) -> MeshType:
        return cls(value)


@dataclass
class MeshStore:
    index: int
    matrix: list | None = field(repr=False)
    position: np.ndarray = field(repr=False)
    indices: np.ndarray | None = field(repr=False)
    normal: np.ndarray | None = field(repr=False)
    material: int | Color
    type: MeshType
    node_id: int

    def get_position3(self):
        return self.position.reshape(-1, 3)

    def get_indices3(self):
        if self.type == MeshType.TRIANGLES:
            return self.indices.reshape(-1, 3)
        elif self.type == MeshType.LINES:
            return self.indices.reshape(-1, 2)


@dataclass
class GroupReference:
    node_id: int | str
    start: int
    length: int


@dataclass
class MergedMesh:
    indices: np.ndarray
    position: np.ndarray
    normal: np.ndarray | None
    material: int
    type: MeshType
    groups: list[GroupReference]


@dataclass
class MeshRef:
    index: int
    node_id: int
