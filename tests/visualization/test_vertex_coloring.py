import os
import pathlib

import numpy as np
import pytest
import trimesh
from trimesh.path.entities import Line
from trimesh.visual.material import PBRMaterial

from ada import Beam


@pytest.fixture
def polygon_mesh():
    vertices = np.asarray([(0, 0, 0), (0, 1, 0), (1, 1, 0)], dtype="float32")
    faces = np.asarray([(0, 1, 2)], dtype="uint8")
    vertex_color = np.asarray([(245, 40, 145), (128, 50, 0), (200, 50, 0)], dtype="uint8")
    new_mesh = trimesh.Trimesh(vertices=vertices, faces=faces, vertex_colors=vertex_color)
    new_mesh.visual.material = PBRMaterial(doubleSided=True)
    return new_mesh


def test_vertex_coloring_simple(polygon_mesh):
    scene = trimesh.Scene()

    assert polygon_mesh.visual.kind == "vertex"

    scene.add_geometry(polygon_mesh, node_name="test", geom_name="test")

    os.makedirs("temp", exist_ok=True)
    scene.export(file_obj="temp/polygon2.glb", file_type=".glb")


def test_polygon_animation_simple(polygon_mesh):
    scene = trimesh.Scene()

    scene.add_geometry(polygon_mesh, node_name="test", geom_name="test")

    # https://github.com/KhronosGroup/glTF-Tutorials/blob/master/gltfTutorial/gltfTutorial_006_SimpleAnimation.md
    # https://github.com/KhronosGroup/glTF-Tutorials/blob/master/gltfTutorial/gltfTutorial_007_Animations.md
    def add_animation_to_tree(tree):
        tree["animations"] = [
            {
                "samplers": [{"input": 2, "interpolation": "LINEAR", "output": 3}],
                "channels": [{"sampler": 0, "target": {"node": 1, "path": "rotation"}}],
            }
        ]

    os.makedirs("temp", exist_ok=True)
    scene.export(file_obj="temp/polygon_animation.glb", file_type=".glb", tree_postprocessor=add_animation_to_tree)


def test_instanced_mapped_geometry():
    bm = Beam("bm1", (0, 0, 0), (1, 0, 0), sec="IPE300")
    obj_mesh = bm.to_obj_mesh()
    new_mesh = obj_mesh.to_trimesh()
    new_mesh.apply_scale(bm.xvec * 0.1)
    meshes = [new_mesh]
    fem = bm.to_fem_obj(0.1, "line")
    for el in fem.elements.lines:
        n1, n2 = el.nodes[0], el.nodes[-1]
        _ = np.array([n1.p, n2.p])
        # new_mesh
        #
        # instanced_mesh = None
        # meshes.append(instanced_mesh)

    scene = trimesh.Scene()

    scene.add_geometry(meshes, bm.name, bm.name)
    scene.export(file_obj="temp/lines.glb", file_type=".glb")


def test_vertex_coloring_advanced():
    neutral_dir = pathlib.Path(__file__).parent.resolve() / "../../files/fem_files/meshes/neutral"
    vertices = np.load(neutral_dir / "vertices.npy")
    faces = np.load(neutral_dir / "faces.npy")
    vertex_color = np.load(neutral_dir / "colors.npy")

    scene = trimesh.Scene()
    new_mesh = trimesh.Trimesh(vertices=vertices, faces=faces, vertex_colors=vertex_color)
    new_mesh.visual.material = PBRMaterial(doubleSided=True)

    scene.add_geometry(new_mesh, node_name="test", geom_name="test")
    os.makedirs("temp", exist_ok=True)
    scene.export(file_obj="temp/planes.glb", file_type=".glb")


def test_single_line_segments():
    scene = trimesh.Scene()
    points = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (1, 1, 1)]
    path = trimesh.load_path(np.asarray(points))
    scene.add_geometry(path)
    scene.export(file_obj="temp/lines.glb", file_type=".glb")


def test_multiple_line_segments():
    from ada.core.vector_utils import rot_matrix

    scene = trimesh.Scene()
    points = np.asarray([(0, 0, 0.5), (1, 0, 0.5), (0, 1, 0.5), (1, 1, 0.5)], dtype=float)
    path = trimesh.path.Path3D(entities=[Line([0, 1]), Line([2, 3])], vertices=points)
    scene.add_geometry(path)

    m3x3 = rot_matrix((0, -1, 0))
    m3x3_with_col = np.append(m3x3, np.array([[0], [0], [0]]), axis=1)
    m4x4 = np.r_[m3x3_with_col, [np.array([0, 0, 0, 1])]]
    scene.apply_transform(m4x4)
    os.makedirs("temp", exist_ok=True)
    scene.export(file_obj="temp/multi_lines.glb", file_type=".glb")
