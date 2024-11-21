from __future__ import annotations

import json
import pathlib
from dataclasses import dataclass, field
from typing import BinaryIO, Iterable

import numpy as np
import trimesh
import trimesh.visual
from trimesh.path.entities import Line

from ada.config import logger
from ada.core.vector_transforms import transform_4x4
from ada.visit.colors import Color, color_dict
from ada.visit.gltf.graph import GraphNode, GraphStore
from ada.visit.gltf.meshes import MergedMesh, MeshRef, MeshStore, MeshType
from ada.visit.gltf.optimize import concatenate_stores
from ada.visit.optimizing import optimize_positions
from ada.visit.utils import m4x4_z_up_rot


@dataclass
class BufferIndex:
    start: int
    length: int


@dataclass
class GltfMergeStore:
    file_path: pathlib.Path | str
    json_data: dict = field(default_factory=dict)
    bin_obj: BinaryIO = field(default=None)
    buffer_locations: dict[int, BufferIndex] = field(default_factory=dict)
    graph: GraphStore = field(init=False)
    split_level: int = 0
    rem_duplicate_vertices: bool = True

    def __post_init__(self):
        json_data, file_obj, buffer_binary_index = GltfMergeStore.load_glb_data(self.file_path)
        self.json_data = json_data
        self.bin_obj = file_obj
        self.buffer_locations = buffer_binary_index
        self.graph = GraphStore.from_json_data(self.json_data, self.split_level)

    def export_merged_meshes_to_glb(self, glb_path: str | pathlib.Path, suffix: str = ""):
        if isinstance(glb_path, str):
            glb_path = pathlib.Path(glb_path)

        scene = trimesh.Scene(base_frame=self.graph.top_level.name)
        scene.metadata["meta"] = self.graph.create_meta(suffix=suffix)

        for material_id, merged_mesh in self.iter_merged_meshes_by_material():
            pbr_mat = trimesh.visual.material.PBRMaterial(
                **self.json_data["materials"][material_id]["pbrMetallicRoughness"]
            )

            merged_mesh_to_trimesh_scene(scene, merged_mesh, pbr_mat, material_id, self.graph)

            # TODO: Embed vertex groups into gltf
            # see https://github.com/KhronosGroup/glTF-Blender-IO/issues/1232

        glb_path.parent.mkdir(parents=True, exist_ok=True)
        # Trimesh automatically transforms by setting up = Y. This will counteract that transform
        if len(scene.geometry) == 0:
            logger.info(f"No meshes found in {self.graph.top_level.name} when attempting to export to GLB")
            return None

        scene.export(glb_path)

    @staticmethod
    def load_glb_data(file_path) -> tuple[dict, BinaryIO, dict]:
        _magic = {"gltf": 1179937895, "json": 1313821514, "bin": 5130562}

        file_obj = open(file_path, "rb")
        start = file_obj.tell()
        head_data = file_obj.read(20)
        head = np.frombuffer(head_data, dtype="<u4")
        length, chunk_length, chunk_type = head[2:]
        if chunk_type != _magic["json"]:
            raise ValueError("no initial JSON header!")
        json_data = json.loads(file_obj.read(int(chunk_length)))

        # Find the location of buffers
        buffer_binary_index = dict()
        buffers_index = 0
        while (file_obj.tell() - start) < length:
            chunk_head = file_obj.read(8)
            buffer_chunk_length, buffer_chunk_type = np.frombuffer(chunk_head, dtype="<u4")
            # make sure we have the right data type
            if buffer_chunk_type != _magic["bin"]:
                raise ValueError("not binary GLTF!")
            buffer_binary_index[buffers_index] = BufferIndex(file_obj.tell(), buffer_chunk_length)
            file_obj.seek(buffer_chunk_length, 1)

        return json_data, file_obj, buffer_binary_index

    def iter_nodes(self) -> Iterable[tuple[int, GraphNode]]:
        for i, node in self.graph.nodes.items():
            yield i, node

    def iter_nodes_per_unique_color(self):
        unique_colors = {i: [] for i, _ in enumerate(self.json_data["materials"])}
        for i, node in self.iter_nodes():
            if len(node.mesh_indices) > 1:
                logger.debug(f"Node {i} has more than one mesh. Skipping '{node.name}'")
                continue
            elif len(node.mesh_indices) == 0:
                logger.debug(f"Node {i} has no mesh. Skipping '{node.name}'")
                continue
            mesh_ref = node.mesh_indices[0]
            if mesh_ref is None:
                continue
            for mesh_type, prim in self.iter_mesh_primitives(mesh_ref.index):
                unique_colors[prim["material"]].append(i)

        for color, nodes in unique_colors.items():
            yield color, nodes

    def iter_merged_meshes_by_material(self) -> Iterable[tuple[int, MergedMesh]]:
        for color, nodes in self.iter_nodes_per_unique_color():
            nodes_iter = self.iter_mesh_stores_from_nodes(nodes)
            merged_mesh = concatenate_stores(nodes_iter)
            if merged_mesh is None:
                logger.info(f"Material {color} has no meshes. Skipping...")
                continue

            if self.rem_duplicate_vertices:
                new_pos, new_indices = optimize_positions(merged_mesh.position, merged_mesh.indices)
                merged_mesh.position = new_pos
                merged_mesh.indices = new_indices

            yield color, merged_mesh

    def iter_mesh_stores_from_nodes(self, nodes: list[int]) -> Iterable[MeshStore]:
        for i in nodes:
            node = self.graph.nodes.get(i)
            mesh_ref = node.mesh_indices[0]
            if mesh_ref is None:
                continue
            for mesh in self.get_meshes(mesh_ref, i):
                yield mesh

    def iter_mesh_primitives(self, mesh_index: int) -> Iterable[tuple[MeshType, dict]]:
        for primitive in self.json_data["meshes"][mesh_index]["primitives"]:
            mesh_type = MeshType.from_int(primitive.get("mode", 4))
            if mesh_type != MeshType.TRIANGLES:
                logger.debug(f"Mesh {mesh_index} is a {mesh_type=} mesh, skipping for now")
                continue
            yield mesh_type, primitive

    def get_meshes(self, mesh_ref: MeshRef, node_id: int) -> list[MeshStore]:
        meshes = []
        for mesh_type, primitive in self.iter_mesh_primitives(mesh_ref.index):
            matrix = self.json_data["nodes"][mesh_ref.node_id].get("matrix", None)
            translation = self.json_data["nodes"][mesh_ref.node_id].get("translation", None)
            indices = self.get_buffer_data(primitive["indices"])
            position = self.get_buffer_data(primitive["attributes"]["POSITION"])
            if matrix is not None:
                new_position = transform_4x4(matrix, position).flatten()
                position = new_position
            if translation is not None:
                new_position = (position.reshape(len(position) // 3, 3) + np.array(translation)).flatten()
                position = new_position
            normal_index = primitive["attributes"].get("NORMAL", None)
            normal = None
            if normal_index is not None:
                normal = self.get_buffer_data(normal_index)

            mesh = MeshStore(
                mesh_ref.index, matrix, position, indices, normal, primitive["material"], mesh_type, node_id
            )
            meshes.append(mesh)

        return meshes

    def get_buffer_data(self, accessor_index: int) -> np.ndarray:
        # https://www.khronos.org/registry/glTF/specs/2.0/glTF-2.0.html#accessor-data-types
        DTYPES = {5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}

        acc = self.json_data["accessors"][accessor_index]
        buff = self.json_data["bufferViews"][acc["bufferView"]]

        # Set the file object to the start of the buffer
        self.bin_obj.seek(self.buffer_locations[buff["buffer"]].start)
        self.bin_obj.seek(buff.get("byteOffset", 0), 1)
        return np.frombuffer(self.bin_obj.read(buff["byteLength"]), dtype=np.dtype(DTYPES.get(acc["componentType"])))


def merged_mesh_to_trimesh_scene(
    scene: trimesh.Scene,
    merged_mesh: MergedMesh | MeshStore,
    pbr_mat: dict | Color,
    buffer_id: int,
    graph_store: GraphStore = None,
):
    vertices = merged_mesh.position.reshape(int(len(merged_mesh.position) / 3), 3)
    if merged_mesh.type == MeshType.TRIANGLES:
        indices = merged_mesh.indices.reshape(int(len(merged_mesh.indices) / 3), 3)
        # Setting process=True will automatically merge duplicated vertices
        mesh = trimesh.Trimesh(vertices=vertices, faces=indices, process=False)
        if isinstance(pbr_mat, Color):
            if pbr_mat.hex == "#000000":
                pbr_mat = Color(*color_dict["light-gray"])
            pbr_mat = trimesh.visual.material.PBRMaterial(
                f"mat{buffer_id}", baseColorFactor=(*pbr_mat.rgb255, pbr_mat.opacity), doubleSided=True
            )
        mesh.visual = trimesh.visual.TextureVisuals(material=pbr_mat)
        mesh.visual.uv = np.zeros((len(mesh.vertices), 2))
    elif merged_mesh.type == MeshType.LINES:
        entities = [Line(x) for x in merged_mesh.indices.reshape(int(len(merged_mesh.indices) / 2), 2)]
        mesh = trimesh.path.Path3D(entities=entities, vertices=vertices)
        # Convert the tuple to a numpy array and reshape it to have one row and X columns
        t_array = np.array(pbr_mat.rgb255).reshape(1, -1)
        result = np.tile(t_array, (len(vertices), 1))
        mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh, vertex_colors=result)
    elif merged_mesh.type == MeshType.POINTS:
        mesh = trimesh.points.PointCloud(vertices=vertices)
        # Convert the tuple to a numpy array and reshape it to have one row and X columns
        t_array = np.array(pbr_mat.rgb255).reshape(1, -1)
        result = np.tile(t_array, (len(vertices), 1))
        mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh, vertex_colors=result)
    else:
        raise NotImplementedError(f"Mesh type {merged_mesh.type} is not supported")

    # Rotate the mesh to set Z up
    mesh.apply_transform(m4x4_z_up_rot)

    if isinstance(merged_mesh, MergedMesh):
        node_name = f"node{buffer_id}"
    else:
        node_name = f"node{buffer_id}_{merged_mesh.node_ref}"

    parent_node_name = graph_store.top_level.name if graph_store else None
    geom_name = f"node{buffer_id}"

    scene.add_geometry(
        mesh,
        node_name=node_name,
        geom_name=geom_name,
        parent_node_name=parent_node_name,
    )

    if graph_store and isinstance(merged_mesh, MergedMesh):
        id_sequence = dict()
        for group in merged_mesh.groups:
            n = None
            if isinstance(group.node_ref, GraphNode):
                n = group.node_ref
            if n is None:
                n = graph_store.nodes.get(group.node_ref)
            if n is None:
                n = graph_store.hash_map.get(group.node_ref)
            if n is None:
                raise ValueError(f"Node {group.node_ref} not found in graph store")

            id_sequence[n.node_id] = (group.start, group.length)

        scene.metadata[f"draw_ranges_node{buffer_id}"] = id_sequence
