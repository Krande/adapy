from __future__ import annotations

import trimesh

from ada.base.physical_objects import BackendGeom
from ada.core.guid import create_guid
from ada.visit.gltf.graph import GraphNode, GraphStore
from ada.visit.render_params import RenderParams


def scene_from_object(physical_object: BackendGeom, params: RenderParams) -> trimesh.Scene:
    from itertools import groupby

    from ada import Pipe
    from ada.occ.tessellating import BatchTessellator
    from ada.visit.gltf.optimize import concatenate_stores
    from ada.visit.gltf.store import merged_mesh_to_trimesh_scene

    bt = BatchTessellator()

    root = GraphNode("world", 0, hash=create_guid())
    graph_store = GraphStore(top_level=root, nodes={0: root})
    node = graph_store.add_node(
        GraphNode(physical_object.name, graph_store.next_node_id(), hash=physical_object.guid, parent=root)
    )

    if isinstance(physical_object, Pipe):
        physical_objects = physical_object.segments
        for seg in physical_objects:
            graph_store.add_node(GraphNode(seg.name, graph_store.next_node_id(), hash=seg.guid, parent=node))
    else:
        physical_objects = [physical_object]

    mesh_stores = list(bt.batch_tessellate(physical_objects))
    scene = trimesh.Scene()
    mesh_map = []
    for mat_id, meshes in groupby(mesh_stores, lambda x: x.material):
        meshes = list(meshes)

        merged_store = concatenate_stores(meshes)
        mesh_map.append((mat_id, meshes, merged_store))

        merged_mesh_to_trimesh_scene(
            scene, merged_store, bt.get_mat_by_id(mat_id), mat_id, graph_store, apply_transform=params.apply_transform
        )

    scene.metadata.update(graph_store.create_meta())
    return scene
