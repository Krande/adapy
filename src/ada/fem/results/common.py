from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ada.fem.formats.general import FEATypes
from ada.fem.shapes.definitions import LineShapes, ShellShapes, SolidShapes


@dataclass
class ElementType:
    type: LineShapes | SolidShapes | ShellShapes
    source_software: FEATypes
    source_type: str


@dataclass
class ElementBlock:
    type: ElementType
    nodes: np.ndarray
    elements: np.ndarray


@dataclass
class Nodes:
    coords: np.ndarray
    identifiers: np.ndarray


@dataclass
class Mesh:
    elements: list[ElementBlock]
    nodes: Nodes


@dataclass
class FieldData:
    name: str
    step: int
    components: list[str]
    values: list[tuple] = field(repr=False)


@dataclass
class FEAResult:
    name: str
    software: str | FEATypes
    results: list[FieldData]
    mesh: Mesh

    def to_gltf(self):
        from ada.visualize.femviz import get_edges_and_faces_from_meshio

        _ = np.asarray(self.mesh.points, dtype="float32")

        edges, faces = get_edges_and_faces_from_meshio(self.mesh)
        _ = np.asarray(edges, dtype="uint16").ravel()
        _ = np.asarray(faces, dtype="uint16").ravel()
