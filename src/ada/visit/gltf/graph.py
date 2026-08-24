from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

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
    #: Who minted the ``stable_key`` values on this store's nodes. Opaque to adapy:
    #: it is stored, emitted and compared, never parsed or interpreted (see
    #: :attr:`GraphNode.stable_key`).
    key_domain: str | None = None

    def __post_init__(self):
        self.num_meshes = sum(len(n.mesh_indices) for n in self.nodes.values())
        if self.hash_map is None:
            self.hash_map = {n.hash: n for n in self.nodes.values()}

    def to_json_hierarchy(self, suffix: str = "") -> dict[str, dict[str, tuple[str, str | int]]]:
        """Emit the scene-extras hierarchy contract.

        Always emits ``id_hierarchy`` (``{node_id: (name, parent_id)}``, root parent
        ``"*"``) plus one ``draw_ranges_node<buffer_id>`` per merged mesh.

        When at least one node carries a :attr:`GraphNode.stable_key`, two *parallel*
        keys are added alongside::

            "node_keys":       {"5": "<domain>:/SOME-ELEMENT-NAME"}
            "node_key_domain": "<domain>"      # only when :attr:`key_domain` is set

        They are deliberately a separate map rather than a third element on the
        ``id_hierarchy`` tuple: every existing reader of that tuple — in this repo and
        in the native writers/readers outside it — keeps working untouched, and a
        reader that does not know about keys simply never looks at them. When no node
        has a key, the returned dict is exactly what it was before keys existed.
        """
        from ada.visit.gltf.store import create_id_sequence

        meta = dict()
        node_keys: dict[str | int, str] = {}
        for n in self.nodes.values().__reversed__():
            if n.parent is not None:
                p_id = n.parent.node_id
                n_name = n.name
            else:
                p_id = "*"
                n_name = n.name + suffix
            meta[n.node_id] = (n_name, p_id)
            if n.stable_key is not None:
                node_keys[n.node_id] = n.stable_key

        data = {"id_hierarchy": meta}
        if node_keys:
            data["node_keys"] = node_keys
            if self.key_domain is not None:
                data["node_key_domain"] = self.key_domain
        for buffer_id, merged_mesh in self.merged_meshes.items():
            data[f"draw_ranges_node{buffer_id}"] = create_id_sequence(self, merged_mesh)

        return data

    def assign_stable_keys_from_hash(self, domain: str, overwrite: bool = False) -> int:
        """Opt in to using each node's ``hash`` (the object guid) as its stable key.

        The guid is the natural identity for objects adapy created itself, and until
        now :meth:`to_json_hierarchy` threw it away. This is the one-line way to keep
        it, under a caller-chosen ``domain`` so the keys stay self-describing.

        Deliberately opt-in rather than a default: turning it on unconditionally would
        add ``node_keys`` to every export ever produced, and a guid is only a *stable*
        identity when the model it came from persists guids across writes — which is
        the caller's knowledge, not this class's.

        Returns the number of nodes keyed.
        """
        self.key_domain = domain
        keyed = 0
        for n in self.nodes.values():
            if n.stable_key is not None and not overwrite:
                continue
            n.stable_key = n.hash
            keyed += 1
        return keyed

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

        # Welds compose in via Part.get_all_welds() — see
        # tessellate_part for the rationale (single-source iterator).
        objects = chain(
            part.get_all_physical_objects(pipe_to_segments=True),
            part.get_all_welds(),
        )
        containers = part.get_all_parts_in_assembly()
        root_node = self.hash_map.get(part.guid)

        for p in chain.from_iterable([containers, objects]):
            if p.guid == root_node.hash:
                continue
            if p.guid in self.hash_map.keys():
                continue
            self._ensure_node(p, root_node)

    def _ensure_node(self, obj, root_node: "GraphNode") -> "GraphNode":
        """Return ``obj``'s graph node, creating it — and any missing ancestor —
        on demand so nothing orphans to the scene root.

        ``pipe_to_segments=True`` (see :meth:`add_nodes_from_part`) yields a pipe's
        segments but never the ``Pipe`` container itself, so each segment's parent
        (the pipe) is absent from ``hash_map``; without this the whole pipe would
        flatten to the root of the selection tree. Walking the parent chain here
        materialises the pipe (and, generally, any skipped intermediate) so the
        segments nest under it and it under the pipe's own part."""
        existing = self.hash_map.get(obj.guid)
        if existing is not None:
            return existing
        parent = getattr(obj, "parent", None)
        parent_node = (
            root_node if parent is None or parent.guid == root_node.hash else self._ensure_node(parent, root_node)
        )
        n = self.add_node(GraphNode(obj.name, self.next_node_id(), hash=obj.guid))
        if parent_node is not None:
            n.parent = parent_node
            parent_node.children.append(n)
        return n

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
    #: Optional domain-qualified identity for the real-world thing this node stands
    #: for, e.g. ``"<domain>:/SOME-ELEMENT-NAME"``. Opaque to adapy — it is carried
    #: and compared for equality, never parsed. Two nodes in two different models
    #: sharing a stable key are the same thing; that is the whole of its meaning.
    #: ``None`` (the default) means "no stable identity", and nothing is emitted.
    stable_key: str | None = field(default=None, repr=False)

    def __post_init__(self):
        self.node_id = str(self.node_id)

    def get_safe_name(self):
        return self.name.replace("/", "")

    def __repr__(self):
        parent_node_id = self.parent.node_id if self.parent is not None else None
        return f"{self.__class__.__name__}(name={self.name}, node_id={self.node_id}, parent_node_id={parent_node_id})"
