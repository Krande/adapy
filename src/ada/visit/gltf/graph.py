from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ada.config import logger
from ada.core.guid import create_guid
from ada.visit.gltf.meshes import GroupReference, MergedMesh, MeshRef

if TYPE_CHECKING:
    from ada import Part


@dataclass
class GraphStore:
    top_level: GraphNode = field(repr=False)
    nodes: dict[int | str, GraphNode] = field(repr=False)
    hash_map: dict[str, GraphNode] = field(repr=False, default=None)
    draw_ranges: list[GroupReference] = field(default_factory=list, repr=False)
    merged_meshes: dict[int, MergedMesh] = field(default_factory=dict, repr=False)
    edge_mappings: dict[int, list[int]] = field(default_factory=dict, repr=False)

    def __post_init__(self):
        self.num_meshes = sum(len(n.mesh_indices) for n in self.nodes.values())
        if self.hash_map is None:
            self.hash_map = {n.hash: n for n in self.nodes.values()}

    def to_json_hierarchy(self, suffix: str = "") -> dict[str, dict[str, tuple[str, str | int]]]:
        from ada.visit.gltf.store import create_id_sequence

        meta = dict()
        for n in self.nodes.values().__reversed__():
            if n.parent is not None:
                p_id = n.parent.node_id
                n_name = n.name
            else:
                p_id = "*"
                n_name = n.name + suffix
            meta[n.node_id] = (n_name, p_id)

        data = {"id_hierarchy": meta}
        for buffer_id, merged_mesh in self.merged_meshes.items():
            data[f"draw_ranges_node{buffer_id}"] = create_id_sequence(self, merged_mesh)

        return data

    def add_node(self, node: GraphNode) -> GraphNode:
        self.nodes[node.node_id] = node
        self.hash_map[node.hash] = node

        return node

    def add_merged_mesh(self, buffer_id: int, merged_mesh: MergedMesh):
        self.merged_meshes[buffer_id] = merged_mesh

    def add_edge_mapping(self, buffer_id: int, mapping: list[int]):
        self.edge_mappings[buffer_id] = mapping

    def add_nodes_from_part(self, part: Part) -> None:
        """Add nodes from Part/Assembly"""
        from itertools import chain

        objects = part.get_all_physical_objects(pipe_to_segments=True)
        containers = part.get_all_parts_in_assembly()
        root_node = self.hash_map.get(part.guid)

        for p in chain.from_iterable([containers, objects]):
            if p.guid == root_node.hash:
                continue
            if p.guid in self.hash_map.keys():
                logger.error(f"Duplicate GUID found for {p}")
                continue
            parent_node = self.hash_map.get(p.parent.guid)
            n = self.add_node(GraphNode(p.name, self.next_node_id(), hash=p.guid))
            if parent_node is not None:
                n.parent = parent_node
                parent_node.children.append(n)

    def next_node_id(self):
        return len(self.nodes.keys())

    def next_buffer_id(self):
        return len(self.merged_meshes.keys())

    @staticmethod
    def from_json_data(data, split_level: int = 3):
        nmap = {i: GraphNode(n["name"], i) for i, n in enumerate(data["nodes"]) if n.get("name") is not None}

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
class GraphNode:
    name: str
    node_id: str | int
    children: list[GraphNode] = field(default_factory=list, repr=False)
    parent: GraphNode | None = field(default=None, repr=False)
    mesh_indices: list[MeshRef] = field(default_factory=list, repr=False)
    hash: str = field(default_factory=create_guid, repr=False)

    def __post_init__(self):
        self.node_id = str(self.node_id)

    def get_safe_name(self):
        return self.name.replace("/", "")

    def __repr__(self):
        parent_node_id = self.parent.node_id if self.parent is not None else None
        return f"{self.__class__.__name__}(name={self.name}, node_id={self.node_id}, parent_node_id={parent_node_id})"
