from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass, field
from enum import Enum
from typing import BinaryIO, Iterable

import numpy as np
import trimesh
import trimesh.visual

from ada.config import logger
from ada.core.vector_utils import rot_matrix, transform
from ada.ifc.utils import create_guid
from ada.visualize.optimizing import optimize_positions

_FLAT_MATRIX = np.identity(4).flatten()
# https://www.khronos.org/registry/glTF/specs/2.0/glTF-2.0.html#accessor-data-types
DTYPES = {5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_magic = {"gltf": 1179937895, "json": 1313821514, "bin": 5130562}


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
class BufferIndex:
    start: int
    length: int


@dataclass
class GltfStore:
    file_path: pathlib.Path | str
    json_data: dict = field(default_factory=dict)
    bin_obj: BinaryIO = field(default=None)
    buffer_locations: dict[int, BufferIndex] = field(default_factory=dict)
    graph: GraphStore = field(init=False)
    split_level: int = 0
    rem_duplicate_vertices: bool = True

    def __post_init__(self):
        json_data, file_obj, buffer_binary_index = GltfStore.load_glb_data(self.file_path)
        self.json_data = json_data
        self.bin_obj = file_obj
        self.buffer_locations = buffer_binary_index
        self.graph = GraphStore.from_json_data(self.json_data, self.split_level)

    def has_no_mesh_data(self):
        if len(self.buffer_locations) == 1 and self.buffer_locations.get(0).length == 0:
            return True
        return False

    def export_merged_meshes_to_glb(self, glb_path: str | pathlib.Path, suffix: str = ""):
        if isinstance(glb_path, str):
            glb_path = pathlib.Path(glb_path)

        scene = trimesh.Scene(base_frame=self.graph.top_level.name)
        scene.metadata["meta"] = self.graph.create_meta(suffix=suffix)

        for material_id, merged_mesh in self.iter_merged_meshes_by_material():
            vertices = merged_mesh.position.reshape(int(len(merged_mesh.position) / 3), 3)
            faces = merged_mesh.indices.reshape(int(len(merged_mesh.indices) / 3), 3)
            # Setting process=True will automatically merge duplicated vertices
            mesh = trimesh.Trimesh(vertices=vertices, faces=faces, process=False)
            pbr_mat = trimesh.visual.material.PBRMaterial(
                **self.json_data["materials"][material_id]["pbrMetallicRoughness"]
            )
            mesh.visual = trimesh.visual.TextureVisuals(material=pbr_mat)

            m3x3 = rot_matrix((0, -1, 0))
            m3x3_with_col = np.append(m3x3, np.array([[0], [0], [0]]), axis=1)
            m4x4 = np.r_[m3x3_with_col, [np.array([0, 0, 0, 1])]]
            mesh.apply_transform(m4x4)

            scene.add_geometry(
                mesh,
                node_name=f"node{material_id}",
                geom_name=f"node{material_id}",
                parent_node_name=self.graph.top_level.name,
            )
            id_sequence = dict()
            for group in merged_mesh.groups:
                n = self.graph.nodes.get(group.node_id)
                id_sequence[n.hash] = (group.start, group.start + group.length - 1)

            scene.metadata[f"id_sequence{material_id}"] = id_sequence

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

    def iter_nodes(self) -> Iterable[tuple[int, Node]]:
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
            if os.getenv("OPT_CONCAT_OFF", None) is not None:
                merged_mesh = self.concatenate_stores(nodes_iter)
            else:
                merged_mesh = self.concatenate_stores_1(nodes_iter)
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
                new_position = transform(matrix, position).flatten()
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
        acc = self.json_data["accessors"][accessor_index]
        buff = self.json_data["bufferViews"][acc["bufferView"]]

        # Set the file object to the start of the buffer
        self.bin_obj.seek(self.buffer_locations[buff["buffer"]].start)
        self.bin_obj.seek(buff.get("byteOffset", 0), 1)
        return np.frombuffer(self.bin_obj.read(buff["byteLength"]), dtype=np.dtype(DTYPES.get(acc["componentType"])))

    @staticmethod
    def concatenate_stores(stores: Iterable[MeshStore]) -> MergedMesh | None:
        stores = list(stores)
        if len(stores) == 0:
            return None
        mesh = stores[0]
        groups = [
            GroupReference(s.node_id, sum(len(l.indices) for l in stores[:i]), len(s.indices))
            for i, s in enumerate(stores)
        ]
        position = np.concatenate([s.position for s in stores], dtype=np.float32)
        indices = np.concatenate(
            [s.indices + sum(len(l.position) // 3 for l in stores[:i]) for i, s in enumerate(stores)], dtype=np.uint32
        )

        normal = None
        if mesh.normal is not None:
            normal = np.concatenate([s.normal for s in stores])

        return MergedMesh(indices, position, normal, mesh.material, mesh.type, groups)

    @staticmethod
    def concatenate_stores_1(stores: Iterable[MeshStore]) -> MergedMesh | None:
        """This variant is faster at the expense of using more memory"""
        stores = list(stores)
        if not stores:
            return None

        groups = []
        position_list = []
        indices_list = []
        normal_list = []
        has_normal = stores[0].normal is not None
        sum_positions = 0
        sum_indices = 0

        for i, s in enumerate(stores):
            groups.append(GroupReference(s.node_id, sum_indices, len(s.indices)))
            position_list.append(s.position)
            indices_list.append(s.indices + sum_positions // 3)
            if has_normal:
                normal_list.append(s.normal)

            sum_positions += len(s.position)
            sum_indices += len(s.indices)

        position = np.concatenate(position_list, dtype=np.float32)
        indices = np.concatenate(indices_list, dtype=np.uint32)
        normal = np.concatenate(normal_list) if has_normal else None

        return MergedMesh(indices, position, normal, stores[0].material, stores[0].type, groups)


@dataclass
class MeshStore:
    index: int
    matrix: list = field(repr=False)
    position: np.ndarray = field(repr=False)
    indices: np.ndarray = field(repr=False)
    normal: np.ndarray | None = field(repr=False)
    material: int
    type: MeshType
    node_id: int


@dataclass
class MergedMesh:
    indices: np.ndarray
    position: np.ndarray
    normal: np.ndarray | None
    material: int
    type: MeshType
    groups: list[GroupReference]


@dataclass
class GroupReference:
    node_id: int
    start: int
    length: int


@dataclass
class GraphStore:
    top_level: Node = field(repr=False)
    nodes: dict[int, Node] = field(repr=False)
    _name_map: dict[str, Node] = field(repr=False, init=False)

    def __post_init__(self):
        self.num_meshes = sum(len(n.mesh_indices) for n in self.nodes.values())
        self._name_map = {n.name: n for n in self.nodes.values()}

    def get_by_name(self, name: str) -> Node:
        return self._name_map.get(name)

    def create_meta(self, suffix: str) -> dict[str, tuple[str, str]]:
        meta = dict()
        for n in self.nodes.values().__reversed__():
            if n.parent is not None:
                p_name = n.parent.hash
                n_name = n.name
            else:
                p_name = "*"
                n_name = n.name + suffix
            meta[n.hash] = (n_name, p_name)
        return meta

    @staticmethod
    def from_json_data(data, split_level: int = 3):
        nmap = {i: Node(n["name"], i) for i, n in enumerate(data["nodes"]) if n.get("name") is not None}

        for i, n in nmap.items():
            mesh = data["nodes"][i].get("mesh", None)
            meshes = []
            if mesh is not None:
                meshes = [MeshRef(mesh, n.node_id)]

            for child_index in data["nodes"][i].get("children", []):
                child = nmap.get(child_index)
                if child is None:
                    mesh_index = data["nodes"][child_index].get("mesh", None)
                    if mesh_index is not None:
                        meshes.append(MeshRef(mesh_index, child_index))
                else:
                    child.parent = n
                    n.children.append(child)
            n.mesh_indices = meshes
        top_level = [x for x in nmap.values() if x.parent is None]

        if len(top_level) != 1:
            raise ValueError("Top level nodes must have exactly one child")

        top_level = top_level[0]
        if split_level == 0:
            return GraphStore(top_level, nmap)

        level = 0
        while True:
            children = top_level.children
            if len(children) != 1:
                raise ValueError("Top level nodes must have exactly one child")
            nmap.pop(top_level.node_id)
            top_level = children[0]

            level += 1
            if level >= split_level - 1:
                break

        # Remove parent of top level as this is superfluous
        top_level.parent = None

        return GraphStore(top_level, nmap)

    def __repr__(self):
        return f"GraphStore({self.top_level}, nodes={len(self.nodes)}, meshes={self.num_meshes})"


@dataclass
class MeshRef:
    index: int
    node_id: int


@dataclass
class Node:
    name: str
    node_id: int
    children: list[Node] = field(default_factory=list, repr=False)
    parent: Node | None = field(default=None, repr=False)
    mesh_indices: list[MeshRef] = field(default_factory=list, repr=False)
    hash: str = field(default_factory=create_guid, repr=False)

    def get_safe_name(self):
        return self.name.replace("/", "")
