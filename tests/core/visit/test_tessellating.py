import os
from itertools import groupby

import trimesh

from ada.occ.tessellating import BatchTessellator
from ada.param_models.primitives_generators import ShapeGenerator
from ada.visit.gltf.optimize import concatenate_stores
from ada.visit.gltf.store import merged_mesh_to_trimesh_scene


def test_shape_grid():
    bg = ShapeGenerator(grid_size=1)
    shape_grid = list(bg.generate_shape_grid())

    bt = BatchTessellator()
    all_shapes = sorted(bt.batch_tessellate(shape_grid), key=lambda x: x.material)

    scene = trimesh.Scene(base_frame=bg.graph.top_level.name)
    scene.metadata["meta"] = bg.graph.create_meta(suffix="")
    mesh_map = []
    for mat_id, meshes in groupby(all_shapes, lambda x: x.material):
        meshes = list(meshes)
        merged_store = concatenate_stores(meshes)
        mesh_map.append((mat_id, meshes, merged_store))
        merged_mesh_to_trimesh_scene(scene, merged_store, bt.get_mat_by_id(mat_id), mat_id, bg.graph)

    # assert mat0
    assert len(mesh_map) == len(scene.geometry)
    mat_id0, meshes0, merged_store0 = mesh_map[0]
    scene0 = scene.geometry.get(f"node{mat_id0}")
    os.makedirs("temp", exist_ok=True)
    scene.export("temp/test.glb")
