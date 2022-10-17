from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ada.fem.formats.general import FEATypes
from ada.fem.shapes.definitions import LineShapes, ShellShapes, SolidShapes

from .field_data import FieldData


@dataclass
class ElementType:
    type: LineShapes | SolidShapes | ShellShapes
    source_software: FEATypes
    source_type: str


@dataclass
class ElementBlock:
    type: ElementType
    nodes: np.ndarray
    identifiers: np.ndarray


@dataclass
class Nodes:
    coords: np.ndarray
    identifiers: np.ndarray


@dataclass
class Mesh:
    elements: list[ElementBlock]
    nodes: Nodes


@dataclass
class FEAResult:
    name: str
    software: str | FEATypes
    results: list[FieldData]
    mesh: Mesh

    def get_steps(self):
        steps = []
        for x in self.results:
            if x.step not in steps:
                steps.append(x.step)
        return steps

    def get_frame(self, frame_num: int, section_point: int, field_variable: str):
        for x in self.results:
            if x.name != field_variable or x.step != frame_num:
                continue

        print("sd")

    def to_gltf(self):
        from ada.visualize.femviz import get_edges_and_faces_from_meshio

        _ = self.mesh.nodes.coords

        edges, faces = get_edges_and_faces_from_meshio(self.mesh)
        _ = np.asarray(edges, dtype="uint16").ravel()
        _ = np.asarray(faces, dtype="uint16").ravel()
