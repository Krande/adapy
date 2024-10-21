# pip install -U pygfx glfw jupyter_rfb pylinalg
# or
# mamba env update -f environment.yml --prune
#
from itertools import groupby

import trimesh

from ada.occ.tessellating import BatchTessellator
from ada.param_models.primitives_generators import (
    ShapeGenerator,
    random_i_beam_at_position,
)
from ada.visit.gltf.optimize import concatenate_stores
from ada.visit.gltf.store import merged_mesh_to_trimesh_scene
from ada.visit.rendering.render_backend import SqLiteBackend
from ada.visit.rendering.render_pygfx import RendererPyGFX


def main():
    grid_size = 4
    bg = ShapeGenerator(grid_size=grid_size)#, shape_function=random_i_beam_at_position)
    shape_grid = list(bg.generate_shape_grid())

    bt = BatchTessellator()
    all_shapes = sorted(bt.batch_tessellate(shape_grid), key=lambda x: x.material)

    scene = trimesh.Scene(base_frame=bg.graph.top_level.name)
    scene.metadata.update(bg.graph.create_meta())

    for mat_id, meshes in groupby(all_shapes, lambda x: x.material):
        merged_store = concatenate_stores(meshes)
        merged_mesh_to_trimesh_scene(scene, merged_store, bt.get_mat_by_id(mat_id), mat_id, bg.graph)

    render = RendererPyGFX(render_backend=SqLiteBackend("temp/meshes.db"))
    render.add_trimesh_scene(scene, "boxes")

    scene.export("temp/shapes.glb")

    render.show()


if __name__ == "__main__":
    main()
