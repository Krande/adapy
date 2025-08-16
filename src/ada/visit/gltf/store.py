from __future__ import annotations

import numpy as np
import trimesh
import trimesh.visual
from trimesh.path.entities import Line

from ada.config import logger
from ada.visit.colors import Color, color_dict
from ada.visit.gltf.graph import GraphNode, GraphStore
from ada.visit.gltf.meshes import MergedMesh, MeshStore, MeshType
from ada.visit.utils import m4x4_z_up_rot


def merged_mesh_to_trimesh_scene(
    scene: trimesh.Scene,
    merged_mesh: MergedMesh | MeshStore,
    pbr_mat: dict | Color,
    buffer_id: int,
    graph_store: GraphStore = None,
    apply_transform: bool = False,
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
        mesh = trimesh.path.Path3D(entities=entities, vertices=vertices, process=False)
        # Convert the tuple to a numpy array and reshape it to have one row and X columns
        t_array = np.array(pbr_mat.rgb255).reshape(1, -1)
        result = np.tile(t_array, (len(vertices), 1))
        mesh.visual = trimesh.visual.ColorVisuals(mesh=mesh, vertex_colors=result)
        # Build expanded edge vertex mapping (per GL_LINES vertex order)
        # For Line entities, each entity has two endpoints; the exported sequence
        # is simply [i0, i1] per entity, stacked in order.
        expanded = []
        try:
            edge_pairs = merged_mesh.indices.reshape(int(len(merged_mesh.indices) / 2), 2)
            for i, j in edge_pairs:
                expanded.append(int(i))
                expanded.append(int(j))
        except Exception:
            expanded = []
        if graph_store is not None and len(expanded) > 0:
            # Store mapping keyed by buffer_id so the animation builder can retrieve it
            graph_store.add_edge_mapping(buffer_id, expanded)
    elif merged_mesh.type == MeshType.POINTS:
        mesh = trimesh.points.PointCloud(vertices=vertices)
    else:
        raise NotImplementedError(f"Mesh type {merged_mesh.type} is not supported")

    # Rotate the mesh to set Z up
    if apply_transform:
        mesh.apply_transform(m4x4_z_up_rot)

    if isinstance(merged_mesh, MergedMesh):
        node_name = f"node{buffer_id}"
    else:
        node_name = f"node{buffer_id}_{merged_mesh.node_ref}"

    parent_node_name = graph_store.top_level.name if graph_store else None
    geom_name = f"node{buffer_id}"

    if graph_store:
        graph_store.add_merged_mesh(buffer_id, merged_mesh)

    return scene.add_geometry(
        mesh,
        node_name=node_name,
        geom_name=geom_name,
        parent_node_name=parent_node_name,
    )


def create_id_sequence(graph_store: GraphStore, merged_mesh: MergedMesh):
    id_sequence = dict()
    if isinstance(merged_mesh, MergedMesh):
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
    else:
        logger.warning(f"{type(merged_mesh)=} is not a MergedMesh")

    return id_sequence
