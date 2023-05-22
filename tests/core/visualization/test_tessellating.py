import os
from itertools import groupby

import trimesh

from ada.occ.tessellating import BatchTessellator
from ada.param_models.primitives_generators import BoxGenerator
from ada.visit.gltf.optimize import concatenate_stores
from ada.visit.gltf.store import merged_mesh_to_trimesh_scene


def test_shape_grid():
    bg = BoxGenerator(grid_size=2)
    shape_grid = bg.generate_box_grid()

    bt = BatchTessellator()
    all_shapes = sorted(bt.batch_tessellate(shape_grid), key=lambda x: x.material)

    scene = trimesh.Scene(base_frame=bg.graph.top_level.name)
    scene.metadata["meta"] = bg.graph.create_meta(suffix='')
    for mat_id, mat in groupby(all_shapes, lambda x: x.material):
        merged_store = concatenate_stores(mat)
        merged_mesh_to_trimesh_scene(scene, merged_store, bt.get_mat_by_id(mat_id), mat_id, bg.graph)

    os.makedirs("temp", exist_ok=True)
    scene.export("temp/test.glb")
