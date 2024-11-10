from __future__ import annotations

from dataclasses import dataclass, field

from ada.core.guid import create_guid
from ada.visit.gltf.meshes import GroupReference, MeshRef


@dataclass
class GraphStore:
    top_level: GraphNode = field(repr=False)
    nodes: dict[int | str, GraphNode] = field(repr=False)
    hash_map: dict[str, GraphNode] = field(repr=False, default=None)
    draw_ranges: list[GroupReference] = field(default_factory=list, repr=False)

    def __post_init__(self):
        self.num_meshes = sum(len(n.mesh_indices) for n in self.nodes.values())
        if self.hash_map is None:
            self.hash_map = {n.hash: n for n in self.nodes.values()}

    def create_meta(self, suffix: str = "") -> dict[str, dict[str, tuple[str, str | int]]]:
        meta = dict()
        for n in self.nodes.values().__reversed__():
            if n.parent is not None:
                p_id = n.parent.node_id
                n_name = n.name
            else:
                p_id = "*"
                n_name = n.name + suffix
            meta[n.node_id] = (n_name, p_id)

        return {"id_hierarchy": meta}

    def add_node(self, node: GraphNode) -> GraphNode:
        self.nodes[node.node_id] = node
        self.hash_map[node.hash] = node

        return node

    def next_node_id(self):
        return len(self.nodes.keys())

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
