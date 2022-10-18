from __future__ import annotations

from dataclasses import dataclass

import meshio
import numpy as np

from ada.fem.formats.general import FEATypes
from ada.fem.shapes.definitions import LineShapes, ShellShapes, SolidShapes

from .field_data import ElementFieldData, NodalFieldData


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
    results: list[ElementFieldData | NodalFieldData]
    mesh: Mesh

    def get_steps(self):
        steps = []
        for x in self.results:
            if x.step not in steps:
                steps.append(x.step)
        return steps

    def to_meshio_mesh(self):
        from .field_data import ElementFieldData, NodalFieldData

        cells = []
        for cb in self.mesh.elements:
            ncopy = cb.nodes.copy()
            for i, v in enumerate(self.mesh.nodes.identifiers):
                ncopy[np.where(ncopy == v)] = i
            cells += [meshio.CellBlock(cell_type=cb.type.type.value.lower(), data=ncopy)]

        cell_data = dict()
        point_data = dict()
        for x in self.results:
            res = x.get_values_only()
            name = f"{x.name} - {x.step}"
            if isinstance(x, NodalFieldData):
                point_data[name] = res
            elif isinstance(x, ElementFieldData):
                cell_data[name] = res
            else:
                raise ValueError()

        return meshio.Mesh(points=self.mesh.nodes.coords, cells=cells, cell_data=cell_data, point_data=point_data)

    def to_gltf(self):
        from ada.visualize.femviz import get_edges_and_faces_from_meshio

        _ = self.mesh.nodes.coords

        edges, faces = get_edges_and_faces_from_meshio(self.mesh)
        _ = np.asarray(edges, dtype="uint16").ravel()
        _ = np.asarray(faces, dtype="uint16").ravel()
