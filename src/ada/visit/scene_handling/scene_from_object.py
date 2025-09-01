from __future__ import annotations

from typing import TYPE_CHECKING

from ada.base.physical_objects import BackendGeom
from ada.config import logger
from ada.visit.gltf.graph import GraphNode

if TYPE_CHECKING:
    import trimesh

    from ada.visit.scene_converter import SceneConverter


def scene_from_object(physical_object: BackendGeom, converter: SceneConverter) -> trimesh.Scene:
    from itertools import groupby

    import trimesh

    from ada import Pipe
    from ada.occ.tessellating import BatchTessellator
    from ada.visit.gltf.optimize import concatenate_stores
    from ada.visit.gltf.store import merged_mesh_to_trimesh_scene

    bt = BatchTessellator()
    params = converter.params
    graph = converter.graph
    root = graph.top_level

    scene = trimesh.Scene(base_frame=converter.graph.top_level.name) if converter.scene is None else converter.scene

    node = graph.add_node(GraphNode(physical_object.name, graph.next_node_id(), hash=physical_object.guid, parent=root))

    if isinstance(physical_object, Pipe):
        physical_objects = physical_object.segments
        for seg in physical_objects:
            graph.add_node(GraphNode(seg.name, graph.next_node_id(), hash=seg.guid, parent=node))
    else:
        physical_objects = [physical_object]

    if params.stream_from_ifc_store:
        logger.warning(
            "Streaming from IFC store is not supported for show() called directly from a physical object. "
            "To do so, you need to call show() from an Assembly object"
        )

    mesh_stores = list(bt.batch_tessellate(physical_objects))

    mesh_map = []
    for mat_id, meshes in groupby(mesh_stores, lambda x: x.material):
        meshes = list(meshes)

        merged_store = concatenate_stores(meshes)
        mesh_map.append((mat_id, meshes, merged_store))

        merged_mesh_to_trimesh_scene(
            scene, merged_store, bt.get_mat_by_id(mat_id), mat_id, graph, apply_transform=params.apply_transform
        )

    return scene
