from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

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
    source_type: str | int


@dataclass
class ElementBlock:
    elem_info: ElementInfo
    node_refs: np.ndarray
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
            for node_ids in block.node_refs[res]:
                nodes = self.nodes.get_node_by_id(node_ids)
                return Elem(elem_id, nodes, block.elem_info.type)

    def get_edges_and_faces_from_mesh(self) -> tuple[np.ndarray, np.ndarray]:
        from ada.fem.shapes import ElemShape
        from ada.fem.shapes import definitions as shape_def

        nmap = {x: i for i, x in enumerate(self.nodes.identifiers)}
        edges = []
        faces = []
        for cell_block in self.elements:
            el_type = cell_block.elem_info.type
            nodes_copy = cell_block.node_refs.copy()
            for old, new in nmap.items():
                nodes_copy[nodes_copy == old] = new

            for elem in nodes_copy:
                elem_shape = ElemShape(el_type, elem)
                edges += elem_shape.edges
                if isinstance(elem_shape.type, shape_def.LineShapes):
                    continue
                faces += elem_shape.faces

        faces = np.array(faces).reshape(int(len(faces) / 3), 3)
        edges = np.array(edges).reshape(int(len(edges) / 2), 2)
        return edges, faces


@dataclass
class FEAResult:
    name: str
    software: str | FEATypes
    results: list[ElementFieldData | NodalFieldData]
    mesh: Mesh

    def __post_init__(self):
        if self.results is None:
            self.results = []

        for res in self.results:
            res._mesh = self.mesh

    def get_steps(self):
        steps = []
        for x in self.results:
            if x.step not in steps:
                steps.append(x.step)
        return steps

    def get_results_grouped_by_field_value(self) -> dict[str, list[ElementFieldData | NodalFieldData]]:
        results = dict()
        for x in self.results:
            if x.name not in results.keys():
                results[x.name] = []
            results[x.name].append(x)
        return results

    def _get_cell_blocks(self):
        cells = []
        for cb in self.mesh.elements:
            ncopy = cb.node_refs.copy()
            for i, v in enumerate(self.mesh.nodes.identifiers):
                ncopy[np.where(ncopy == v)] = i
            cells += [meshio.CellBlock(cell_type=cb.elem_info.type.value.lower(), data=ncopy)]
        return cells

    def _get_point_and_cell_data(self) -> tuple[dict, dict]:
        from .field_data import ElementFieldData, NodalFieldData

        cell_data = dict()
        point_data = dict()
        for key, values in self.get_results_grouped_by_field_value().items():
            for x in values:
                res = x.get_all_values()
                name = f"{x.name} - {x.step}" if len(values) > 1 else x.name
                if isinstance(x, NodalFieldData):
                    point_data[name] = res
                elif isinstance(x, ElementFieldData) and x.field_pos == x.field_pos.NODAL:
                    point_data[name] = res
                elif isinstance(x, ElementFieldData) and x.field_pos == x.field_pos.INT:
                    raise NotImplementedError("Currently not supporting element data directly from int. points")
                else:
                    raise ValueError()
        return cell_data, point_data

    def to_meshio_mesh(self) -> meshio.Mesh:
        cells = self._get_cell_blocks()
        cell_data, point_data = self._get_point_and_cell_data()

        return meshio.Mesh(points=self.mesh.nodes.coords, cells=cells, cell_data=cell_data, point_data=point_data)

    def to_xdmf(self, filepath):
        cells = self._get_cell_blocks()
        with meshio.xdmf.TimeSeriesWriter(filepath) as writer:
            writer.write_points_cells(self.mesh.nodes.coords, cells)
            for key, values in self.get_results_grouped_by_field_value().items():
                for x in values:
                    res = x.get_all_values()
                    name = x.name
                    point_data = dict()
                    if isinstance(x, NodalFieldData):
                        point_data[name] = res
                    elif isinstance(x, ElementFieldData) and x.field_pos == x.field_pos.NODAL:
                        point_data[name] = res
                    elif isinstance(x, ElementFieldData) and x.field_pos == x.field_pos.INT:
                        raise NotImplementedError("Currently not supporting element data directly from int. points")
                    else:
                        raise ValueError()

                    writer.write_data(x.step, point_data=point_data)

    def get_data(self, field: str, step: int):
        steps = self.get_results_grouped_by_field_value().get(field)
        all_field_data = [x for x in steps if x.step == step]
        if len(all_field_data) != 1:
            raise ValueError("Non-unique results of field data")

        field_data = all_field_data[0]
        return field_data.get_all_values()

    def _colorize_data(self, field: str, step: int, colorize_function: Callable = None):
        from ada.visualize.colors import DataColorizer

        data = self.get_data(field, step)
        vertex_colors = DataColorizer.colorize_data(data, func=colorize_function)
        return np.array([[i * 255 for i in x] + [1] for x in vertex_colors], dtype=np.int32)

    def _warp_data(self, vertices: np.ndarray, field: str, step, scale: float = 1.0):
        data = self.get_data(field, step)

        result = vertices + data[:, :3] * scale
        return result

    def to_gltf(self, dest_file, step: int, field: str, warp_field=None, warp_step=None, warp_scale=None, cfunc=None):
        import trimesh
        from trimesh.path.entities import Line
        from trimesh.visual.material import PBRMaterial

        from ada.core.vector_utils import rot_matrix

        dest_file = pathlib.Path(dest_file).resolve().absolute()

        vertices = self.mesh.nodes.coords
        edges, faces = self.mesh.get_edges_and_faces_from_mesh()

        # Colorize data
        vertex_color = self._colorize_data(field, step, cfunc)

        # Warp data
        if warp_field is not None:
            warped_vertices = self._warp_data(vertices, warp_field, warp_step, warp_scale)
            vertices = warped_vertices

        new_mesh = trimesh.Trimesh(vertices=vertices, faces=faces, vertex_colors=vertex_color)
        new_mesh.visual.material = PBRMaterial(doubleSided=True)

        entities = [Line(x) for x in edges]
        edge_mesh = trimesh.path.Path3D(entities=entities, vertices=vertices)

        scene = trimesh.Scene()
        scene.add_geometry(new_mesh, node_name=self.name, geom_name="faces")
        scene.add_geometry(edge_mesh, node_name=f"{self.name}_edges", geom_name="edges", parent_node_name=self.name)

        # Trimesh automatically transforms by setting up = Y. This will counteract that transform
        m3x3 = rot_matrix((0, -1, 0))
        m3x3_with_col = np.append(m3x3, np.array([[0], [0], [0]]), axis=1)
        m4x4 = np.r_[m3x3_with_col, [np.array([0, 0, 0, 1])]]
        scene.apply_transform(m4x4)

        os.makedirs(dest_file.parent, exist_ok=True)
        print(f'Writing Visual Mesh to "{dest_file}"')
        with open(dest_file, "wb") as f:
            scene.export(file_obj=f, file_type=dest_file.suffix[1:])
