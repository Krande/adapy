from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import meshio
import numpy as np

from ada.fem.formats.general import FEATypes
from ada.fem.shapes.definitions import LineShapes, ShellShapes, SolidShapes

from .field_data import ElementFieldData, NodalFieldData

if TYPE_CHECKING:
    from ada import Node
    from ada.fem import Elem


@dataclass
class ElementInfo:
    type: LineShapes | SolidShapes | ShellShapes
    source_software: FEATypes
    source_type: str


@dataclass
class ElementBlock:
    elem_info: ElementInfo
    nodes: np.ndarray
    identifiers: np.ndarray


@dataclass
class FemNodes:
    coords: np.ndarray
    identifiers: np.ndarray

    def get_node_by_id(self, node_id: int | list[int]) -> list[Node]:
        from ada import Node

        if isinstance(node_id, int):
            node_id = [node_id]
        node_indices = [np.where(self.identifiers == x)[0][0] for x in node_id]
        return [Node(x, node_id[i]) for i, x in enumerate(self.coords[node_indices])]


@dataclass
class Mesh:
    elements: list[ElementBlock]
    nodes: FemNodes

    def get_elem_by_id(self, elem_id: int) -> Elem:
        from ada.fem import Elem

        for block in self.elements:
            res = np.where(block.identifiers == elem_id)
            for node_ids in block.nodes[res]:
                nodes = self.nodes.get_node_by_id(node_ids)
                return Elem(elem_id, nodes, block.elem_info.type)


@dataclass
class FEAResult:
    name: str
    software: str | FEATypes
    results: list[ElementFieldData | NodalFieldData]
    mesh: Mesh

    def __post_init__(self):
        for res in self.results:
            res._mesh = self.mesh

    def get_steps(self):
        steps = []
        for x in self.results:
            if x.step not in steps:
                steps.append(x.step)
        return steps

    def get_results_grouped_by_field_value(self) -> dict:
        results = dict()
        for x in self.results:
            if x.name not in results.keys():
                results[x.name] = []
            results[x.name].append(x)
        return results

    def _get_cell_blocks(self):
        cells = []
        for cb in self.mesh.elements:
            ncopy = cb.nodes.copy()
            for i, v in enumerate(self.mesh.nodes.identifiers):
                ncopy[np.where(ncopy == v)] = i
            cells += [meshio.CellBlock(cell_type=cb.elem_info.type.value.lower(), data=ncopy)]
        return cells

    def to_meshio_mesh(self):
        from .field_data import ElementFieldData, NodalFieldData

        cells = self._get_cell_blocks()

        cell_data = dict()
        point_data = dict()
        for key, values in self.get_results_grouped_by_field_value().items():
            for x in values:
                res = x.get_values_only()
                name = f"{x.name} - {x.step}" if len(values) > 1 else x.name
                if isinstance(x, NodalFieldData):
                    point_data[name] = res
                elif isinstance(x, ElementFieldData):
                    cell_data[name] = res
                else:
                    raise ValueError()

        return meshio.Mesh(points=self.mesh.nodes.coords, cells=cells, cell_data=cell_data, point_data=point_data)

    def to_xdmf(self, filepath):
        cells = self._get_cell_blocks()
        with meshio.xdmf.TimeSeriesWriter(filepath) as writer:
            writer.write_points_cells(self.mesh.nodes.coords, cells)
            for key, values in self.get_results_grouped_by_field_value().items():
                if key != "U":
                    continue
                for x in values:
                    point_data = None
                    writer.write_data(x.step, point_data=point_data)

    def to_gltf(self):
        from ada.visualize.femviz import get_edges_and_faces_from_meshio

        _ = self.mesh.nodes.coords

        edges, faces = get_edges_and_faces_from_meshio(self.mesh)
        _ = np.asarray(edges, dtype="uint16").ravel()
        _ = np.asarray(faces, dtype="uint16").ravel()
