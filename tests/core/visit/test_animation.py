import os

import numpy as np
import trimesh

from ada.api.animations import Animation
from ada.visit.scene_converter import SceneConverter


def test_polygon_animation_simple(polygon_mesh, tmp_path):
    scene = trimesh.Scene()

    scene.add_geometry(polygon_mesh, node_name="test", geom_name="test")

    # https://github.com/KhronosGroup/glTF-Tutorials/blob/master/gltfTutorial/gltfTutorial_006_SimpleAnimation.md
    # https://github.com/KhronosGroup/glTF-Tutorials/blob/master/gltfTutorial/gltfTutorial_007_Animations.md
    # Debugging existing glb/gltf files containing animations: https://3d-tile-content-inspector.vercel.app/
    # Validation of created glb/gltf file: https://github.khronos.org/glTF-Validator/

    def add_animation_to_buffer(buffer_items, tree, node_no=1):
        from trimesh.exchange.gltf import _data_append

        deform = np.array(
            [[0.07455, 0.13965, -0.02597], [0.03956, -0.02361, 0.03978], [-0.14752, -0.10503, -0.04253]],
            dtype="float32",
        )

        pos = _data_append(
            acc=tree["accessors"],
            buff=buffer_items,
            blob={"componentType": 5126, "type": "VEC3"},
            data=deform,
        )

        val1 = np.array(
            [0.00000, 0.04167, 0.08333, 0.12500, 0.16667, 0.20833, 0.25000, 0.29167, 0.33333, 0.37500, 0.41667],
            dtype="float32",
        )
        input_val = _data_append(
            acc=tree["accessors"],
            buff=buffer_items,
            blob={"componentType": 5126, "type": "SCALAR"},
            data=val1,
        )
        val2 = np.array(
            [0.00000, 0.02800, 0.10400, 0.21600, 0.35200, 0.50000, 0.64800, 0.78400, 0.89600, 0.97200, 1.00000],
            dtype="float32",
        )
        output_val = _data_append(
            acc=tree["accessors"],
            buff=buffer_items,
            blob={"componentType": 5126, "type": "SCALAR"},
            data=val2,
        )

        # Update tree with data added to buffer
        tree["animations"] = [
            {
                "name": "animation1",
                "samplers": [{"input": input_val, "interpolation": "LINEAR", "output": output_val}],
                "channels": [{"sampler": 0, "target": {"node": node_no, "path": "weights"}}],
            }
        ]
        mesh_no = tree["nodes"][node_no]["mesh"]
        mesh = tree["meshes"][mesh_no]

        mesh["extras"]["targetNames"] = ["Deformed"]
        mesh["weights"] = [mesh_no]

        primitive = mesh["primitives"][0]
        primitive.pop("mode")
        primitive["targets"] = [{"POSITION": pos}]

    os.makedirs(tmp_path, exist_ok=True)
    scene.export(
        file_obj=tmp_path / "polygon_animation.glb",
        file_type=".glb",
        buffer_postprocessor=add_animation_to_buffer,
    )


def test_single_polygon_animate_using_store(polygon_mesh, tmp_path):
    scene = trimesh.Scene()

    node_name = scene.add_geometry(polygon_mesh, node_name="test", geom_name="test")
    node_idx = [i for i, n in enumerate(scene.graph.nodes) if n == node_name][0]

    animation_store = SceneConverter(None)

    animation = Animation(
        "squirmy_poly_1",
        [0, 2, 4],
        deformation_weights_keyframes=[0, 1, 0],
        deformation_shape=[
            [0.07455, 0.13965, -0.02597],
            [0.03956, -0.02361, 0.03978],
            [-0.14752, -0.10503, -0.04253],
        ],
        node_idx=node_idx,
    )
    animation_store.add_animation(animation)

    scene.export(
        file_obj=tmp_path / "polygon_animation_using_store.glb",
        file_type=".glb",
        buffer_postprocessor=animation_store.buffer_postprocessor,
        tree_postprocessor=animation_store.tree_postprocessor,
    )


def test_single_polygon_multiple_animations(polygon_mesh, tmp_path):
    scene = trimesh.Scene()

    node_name = scene.add_geometry(polygon_mesh, node_name="test", geom_name="test")
    node_idx = [i for i, n in enumerate(scene.graph.nodes) if n == node_name][0]

    animation_store = SceneConverter(None)

    time_keys = [0, 2, 4]
    def_weights_keys = [0, 1, 0]

    origin_vertices = [
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0],
        [0.0, 0.0, 0.0],
    ]

    move_vertice = [0.5, 0.5, 0.0]
    for i in range(0, 3):
        current_vertices = origin_vertices.copy()
        current_vertices[i] = move_vertice

        animation = Animation(
            f"squirmy_poly_{i}",
            time_keys,
            deformation_weights_keyframes=def_weights_keys,
            deformation_shape=current_vertices,
            node_idx=node_idx,
        )
        animation_store.add_animation(animation)

    os.makedirs(tmp_path, exist_ok=True)
    scene.export(
        file_obj=tmp_path / "animated_poly_x2.glb",
        file_type=".glb",
        buffer_postprocessor=animation_store.buffer_postprocessor,
        tree_postprocessor=animation_store.tree_postprocessor,
    )
