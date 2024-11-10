from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, Iterable, Literal

import meshio
import numpy as np

from ada.config import logger
from ada.core.guid import create_guid
from ada.fem.formats.general import FEATypes
from ada.fem.shapes.definitions import LineShapes, MassTypes, ShellShapes, SolidShapes
from ada.visit.deprecated.websocket_server import send_to_viewer
from ada.visit.gltf.graph import GraphNode, GraphStore
from ada.visit.gltf.meshes import GroupReference, MergedMesh, MeshType
from ada.visit.renderer_manager import RenderParams

from ...comms.fb_model_gen import FilePurposeDC
from .field_data import ElementFieldData, NodalFieldData, NodalFieldType

if TYPE_CHECKING:
    from ada import Material, Node, Section
    from ada.fem import Elem, FemSet
    from ada.fem.results.concepts import EigenDataSummary


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
        from typing import Iterable

        from ada import Node

        if isinstance(node_id, Iterable) is False:
            node_id = [node_id]

        node_indices = [np.where(self.identifiers == x)[0][0] for x in node_id]
        return [Node(x, int(node_id[i])) for i, x in enumerate(self.coords[node_indices])]


@dataclass
class Mesh:
    elements: list[ElementBlock]
    nodes: FemNodes

    sections: dict[int, Section] = None
    materials: dict[int, Material] = None
    vectors: dict[int, list] = None
    elem_data: np.ndarray = None  # el_id, mat_id, sec_id, vec_id
    sets: dict[str, FemSet] = None

    def get_elem_by_id(self, elem_id: int) -> Elem:
        from ada.base.types import GeomRepr
        from ada.fem import Elem, FemSection, FemSet

        el_id, mat_id, sec_id, vec_id = self.elem_data[np.where(self.elem_data[:, 0] == elem_id)[0], :][0]
        mat = self.materials.get(int(mat_id))
        sec = self.sections.get(int(sec_id))
        vec = self.vectors.get(int(vec_id))

        elem = None
        for block in self.elements:
            res = np.where(block.identifiers == elem_id)
            for node_ids in block.node_refs[res]:
                nodes = self.nodes.get_node_by_id(node_ids)
                elem = Elem(elem_id, nodes, block.elem_info.type)
                break

        fs = FemSection(f"FS{sec_id}", GeomRepr.LINE, FemSet(f"El{el_id}", [elem]), mat, sec, local_z=vec)
        elem.fem_sec = fs
        return elem

    def get_edges_and_faces_from_mesh(self) -> tuple[np.ndarray, np.ndarray]:
        from ada.fem.shapes import ElemShape
        from ada.fem.shapes import definitions as shape_def

        nmap = {x: i for i, x in enumerate(self.nodes.identifiers)}
        keys = np.array(list(nmap.keys()))

        edges = []
        faces = []
        for cell_block in self.elements:
            el_type = cell_block.elem_info.type

            nodes_copy = cell_block.node_refs.copy()
            nodes_copy[np.isin(nodes_copy, keys)] = np.vectorize(nmap.get)(nodes_copy[np.isin(nodes_copy, keys)])

            for elem in nodes_copy:
                elem_shape = ElemShape(el_type, elem)
                if elem_shape.type in (MassTypes.MASS,):
                    continue
                try:
                    edges += elem_shape.edges
                except IndexError as e:
                    logger.error(e)
                    continue
                if isinstance(elem_shape.type, (shape_def.LineShapes, shape_def.ConnectorTypes)):
                    continue
                faces += elem_shape.get_faces()

        faces = np.array(faces).reshape(int(len(faces) / 3), 3)
        edges = np.array(edges).reshape(int(len(edges) / 2), 2)
        return edges, faces

    def create_mesh_stores(
        self,
        parent_name: str,
        shell_color,
        line_color,
        points_color,
        graph: GraphStore,
        parent_node: GraphNode,
        use_solid_beams=False,
    ) -> tuple[MergedMesh, MergedMesh, MergedMesh]:
        from ada.fem.shapes import ElemShape
        from ada.fem.shapes import definitions as shape_def

        face_node = graph.add_node(
            GraphNode(parent_name + "_sh", graph.next_node_id(), hash=create_guid(), parent=parent_node)
        )
        line_node = None
        if use_solid_beams is False:
            line_node = graph.add_node(
                GraphNode(parent_name + "_li", graph.next_node_id(), hash=create_guid(), parent=parent_node)
            )

        points_node = graph.add_node(
            GraphNode(parent_name + "_po", graph.next_node_id(), hash=create_guid(), parent=parent_node)
        )

        nmap = {x: i for i, x in enumerate(self.nodes.identifiers)}
        keys = np.array(list(nmap.keys()))

        edges = []
        faces = []
        sh_groups = []
        li_groups = []

        for cell_block in self.elements:
            el_type = cell_block.elem_info.type

            nodes_copy = cell_block.node_refs.copy()
            nodes_copy[np.isin(nodes_copy, keys)] = np.vectorize(nmap.get)(nodes_copy[np.isin(nodes_copy, keys)])
            if use_solid_beams and isinstance(el_type, (shape_def.LineShapes, shape_def.ConnectorTypes)):
                continue

            el_idmap = {i: x for i, x in enumerate(cell_block.identifiers)}

            for elem_ref, elem in enumerate(nodes_copy, start=0):
                elem_id = el_idmap[elem_ref]
                elem_shape = ElemShape(el_type, elem)
                if elem_shape.type in (MassTypes.MASS,):
                    continue

                new_edges = elem_shape.edges
                edges += new_edges

                if line_node is not None:
                    li_s = len(edges)
                    node = graph.add_node(
                        GraphNode(f"Li{elem_id}", graph.next_node_id(), hash=create_guid(), parent=line_node)
                    )
                    li_groups.append(GroupReference(node, li_s, len(new_edges)))

                if isinstance(elem_shape.type, (shape_def.LineShapes, shape_def.ConnectorTypes)):
                    continue

                face_s = len(faces)
                new_faces = elem_shape.get_faces()
                faces += new_faces
                node = graph.add_node(
                    GraphNode(f"EL{elem_id}", graph.next_node_id(), hash=create_guid(), parent=face_node)
                )
                sh_groups.append(GroupReference(node, face_s, len(new_faces)))

        coords = self.nodes.coords.flatten()
        po_groups = []
        for i, n in enumerate(sorted(self.nodes.identifiers)):
            nid = graph.next_node_id()
            node = graph.add_node(GraphNode(f"P{int(n)}", nid, parent=points_node))
            po_groups.append(GroupReference(node, nid, 1))

        edges = MergedMesh(np.array(edges), coords, None, line_color, MeshType.LINES, groups=li_groups)
        points = MergedMesh(None, coords, None, points_color, MeshType.POINTS, groups=po_groups)
        face_mesh = MergedMesh(np.array(faces), coords, None, shell_color, MeshType.TRIANGLES, groups=sh_groups)

        return points, edges, face_mesh


@dataclass
class FEAResult:
    name: str
    software: str | FEATypes
    results: list[ElementFieldData | NodalFieldData]
    mesh: Mesh
    results_file_path: pathlib.Path = None
    step_name_map: dict[int | float, str] = None
    description: str = None

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

    def get_data_by_field_and_elem_ids(
        self, field: str, elem_ids: list[int], int_points: list[int] = None
    ) -> list[ElementFieldData]:
        data = self.get_results_grouped_by_field_value()
        values = data.get(field)
        output_res = []
        for sdata in values:
            output_res.append(sdata.get_by_element_id(elem_ids, int_points))

        return output_res

    def get_data_by_field_name_and_set_name(self, field, set_name, int_points=None) -> list[ElementFieldData]:
        fs = self.mesh.sets.get(set_name)
        return self.get_data_by_field_and_elem_ids(field, fs.members, int_points)

    def get_field_value_by_name(
        self, name: str, step: int = None
    ) -> ElementFieldData | NodalFieldData | list[ElementFieldData | NodalFieldData]:
        data = self.get_results_grouped_by_field_value()
        values = data.get(name)
        if values is None:
            raise ValueError(f"Unable to find field data '{name}'. Available are {list(data.keys())}")

        if len(values) == 1:
            return values[0]

        if step is not None:
            val_selected = [val for val in values if val.step == step]
            if val_selected == 0:
                raise ValueError(f"Unable to find step=={step}. Available steps are {[x.step for x in values]}")

            return val_selected[0]

        return values

    def iter_results_by_field_value(self) -> Iterable[ElementFieldData | NodalFieldData]:
        for x in self.results:
            yield x

    def get_data(self, field: str, step: int):
        steps = self.get_results_grouped_by_field_value().get(field)
        if step == -1:
            field_data = list(sorted(steps, key=lambda x: x.step))[-1]
        else:
            all_field_data = [x for x in steps if x.step == step]
            if len(all_field_data) != 1:
                raise ValueError(
                    f"Found {len(all_field_data)} results of field data based on step {step}.\n"
                    f"Available steps are {[x.step for x in steps]}"
                )

            field_data = all_field_data[0]

        return field_data.get_all_values()

    def _get_cell_blocks(self):
        cells = []
        for cb in self.mesh.elements:
            cell_type = cb.elem_info.type.value.lower()
            ncopy = cb.node_refs.copy()
            for i, v in enumerate(self.mesh.nodes.identifiers):
                ncopy[np.where(ncopy == v)] = i
            cells += [meshio.CellBlock(cell_type=cell_type, data=ncopy)]
        return cells

    def _get_point_and_cell_data(self) -> tuple[dict, dict]:
        from .field_data import ElementFieldData, NodalFieldData

        cell_data = dict()
        point_data = dict()
        for values in self.get_results_grouped_by_field_value().values():
            for x in values:
                res = x.get_all_values()
                name = f"{x.name} - {x.step}" if len(values) > 1 else x.name
                if isinstance(x, NodalFieldData):
                    point_data[name] = res
                elif isinstance(x, ElementFieldData) and x.field_pos == x.field_pos.NODAL:
                    point_data[name] = res
                elif isinstance(x, ElementFieldData) and x.field_pos == x.field_pos.INT:
                    if isinstance(res, dict):
                        for key, value in res.items():
                            cell_data[key] = value
                    else:
                        cell_data[name] = res
                else:
                    raise ValueError()

        return cell_data, point_data

    def _colorize_data(self, field: str, step: int, colorize_function: Callable = None):
        from ada.visit.colors import DataColorizer

        data = self.get_data(field, step)
        vertex_colors = DataColorizer.colorize_data(data, func=colorize_function)
        return np.array([[i * 255 for i in x] + [1] for x in vertex_colors], dtype=np.int32)

    def _warp_data(self, vertices: np.ndarray, field: str, step, scale: float = 1.0):
        data = self.get_data(field, step)

        result = vertices + data[:, :3] * scale
        return result

    def to_meshio_mesh(self, make_3xn_dofs=True) -> meshio.Mesh:
        cells = self._get_cell_blocks()
        cell_data, point_data = self._get_point_and_cell_data()

        mesh = meshio.Mesh(points=self.mesh.nodes.coords, cells=cells, cell_data=cell_data, point_data=point_data)

        # RMED has 6xN DOF's vertex vectors, but VTU has 3xN DOF's vectors
        if make_3xn_dofs:
            new_fields = {}
            for key, field in mesh.point_data.items():
                if field.shape[1] == 6:
                    new_fields[key] = np.array_split(field, 2, axis=1)[0]
                else:
                    new_fields[key] = field

            mesh.point_data = new_fields

        return mesh

    def to_vtu(self, filepath, make_3xn_dofs=True):
        from ada.fem.formats.vtu.write import write_to_vtu_file

        cell_data, point_data = self._get_point_and_cell_data()

        write_to_vtu_file(self.mesh.nodes, self.mesh.elements, point_data, cell_data, filepath)

    def to_fem_file(self, fem_file: str | pathlib.Path):
        if isinstance(fem_file, str):
            fem_file = pathlib.Path(fem_file)

        mesh = self.to_meshio_mesh()
        mesh.write(fem_file)

    def to_trimesh(
        self, step: int, field: str, warp_field: str = None, warp_step: int = None, warp_scale: float = None, cfunc=None
    ):
        import trimesh
        from trimesh.path.entities import Line
        from trimesh.visual.material import PBRMaterial

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
        return scene

    def to_gltf(self, dest_file, step: int, field: str, warp_field=None, warp_step=None, warp_scale=None, cfunc=None):
        from ...core.vector_transforms import rot_matrix

        dest_file = pathlib.Path(dest_file).resolve().absolute()
        scene = self.to_trimesh(step, field, warp_field, warp_step, warp_scale, cfunc)

        # Trimesh automatically transforms by setting up = Y. This will counteract that transform
        m3x3 = rot_matrix((0, -1, 0))
        m3x3_with_col = np.append(m3x3, np.array([[0], [0], [0]]), axis=1)
        m4x4 = np.r_[m3x3_with_col, [np.array([0, 0, 0, 1])]]
        scene.apply_transform(m4x4)

        os.makedirs(dest_file.parent, exist_ok=True)
        print(f'Writing Visual Mesh to "{dest_file}"')
        with open(dest_file, "wb") as f:
            scene.export(file_obj=f, file_type=dest_file.suffix[1:])

    def show(
        self,
        step: int = None,
        field: str = None,
        warp_field: str = None,
        warp_step: int = None,
        warp_scale: float = 1.0,
        cfunc=None,
        renderer: Literal["react", "pygfx"] = "react",
        host="localhost",
        port=8765,
        server_exe: pathlib.Path = None,
        server_args: list[str] = None,
        run_ws_in_thread=False,
        unique_id=None,
        purpose: FilePurposeDC = FilePurposeDC.ANALYSIS,
        params_override: RenderParams = None,
        ping_timeout=1,
    ):
        from ada.visit.renderer_manager import (
            FEARenderParams,
            RendererManager,
            RenderParams,
        )

        if renderer == "pygfx":
            scene = self.to_trimesh(step, field, warp_field, warp_step, warp_scale, cfunc)
            send_to_viewer(scene)
            return None

        # Use RendererManager to handle renderer setup and WebSocket connection
        renderer_manager = RendererManager(
            renderer=renderer,
            host=host,
            port=port,
            server_exe=server_exe,
            server_args=server_args,
            run_ws_in_thread=run_ws_in_thread,
            ping_timeout=ping_timeout,
        )

        fea_params = FEARenderParams(
            step=step,
            field=field,
            warp_field=warp_field,
            warp_step=warp_step,
            warp_scale=warp_scale,
        )
        if params_override is None:
            params_override = RenderParams(
                unique_id=unique_id,
                auto_sync_ifc_store=False,
                stream_from_ifc_store=False,
                add_ifc_backend=False,
                purpose=purpose,
                fea_params=fea_params,
            )

        # Set up the renderer and WebSocket server
        renderer_instance = renderer_manager.render(self, params_override)
        return renderer_instance

    def get_eig_summary(self) -> EigenDataSummary:
        """If the results are eigenvalue results, this method will return a summary of the eigenvalues and modes"""
        from ada.fem.results.eigenvalue import EigenDataSummary, EigenMode

        modes = []
        for x in self.results:
            if isinstance(x, NodalFieldData) and x.field_type != NodalFieldType.DISP:
                continue
            m = EigenMode(x.step, f_hz=x.eigen_freq, eigenvalue=x.eigen_value)
            modes.append(m)
        return EigenDataSummary(modes)
