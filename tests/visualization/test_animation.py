import os

import trimesh


def test_polygon_animation_simple(polygon_mesh):
    scene = trimesh.Scene()

    scene.add_geometry(polygon_mesh, node_name="test", geom_name="test")

    # https://github.com/KhronosGroup/glTF-Tutorials/blob/master/gltfTutorial/gltfTutorial_006_SimpleAnimation.md
    # https://github.com/KhronosGroup/glTF-Tutorials/blob/master/gltfTutorial/gltfTutorial_007_Animations.md
    # Debugging existing glb/gltf files containing animations: https://3d-tile-content-inspector.vercel.app/
    def add_animation_to_tree(tree):
        tree["animations"] = [
            {
                "name": "",
                "samplers": [{"input": 2, "interpolation": "LINEAR", "output": 3}],
                "channels": [{"sampler": 0, "target": {"node": 1, "path": "rotation"}}],
            }
        ]

    def add_animation_to_buffer(buffer_items, tree):
        from trimesh.exchange.gltf import _data_append, uint32

        _ = _data_append(
            acc=tree["accessors"],
            buff=buffer_items,
            blob={"componentType": 5125, "type": "SCALAR"},
            data=polygon_mesh.faces.astype(uint32),
        )

    os.makedirs("temp", exist_ok=True)

    # Todo: Add support for this in the next release of AdaPy
    scene.export(
        file_obj="temp/polygon_animation.glb",
        file_type=".glb",
        tree_postprocessor=add_animation_to_tree,
        # buffer_postprocessor=add_animation_to_buffer,
    )
