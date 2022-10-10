import os

import numpy as np
import trimesh

from ada import Beam

os.makedirs("temp", exist_ok=True)


def test_vertex_coloring_simple():
    vertices = np.asarray([(0, 0, 0), (0, 1, 0), (1, 1, 0)], dtype="float32")
    faces = np.asarray([(0, 1, 2)], dtype="uint8")
    vertex_color = np.asarray([(245, 40, 145), (128, 50, 0), (200, 50, 0)], dtype="uint8")

    scene = trimesh.Scene()
    new_mesh = trimesh.Trimesh(vertices=vertices, faces=faces, vertex_colors=vertex_color, face_colors=None)
    if new_mesh.visual.kind != "vertex":
        raise ValueError("Vertex coloring is not applied")

    scene.add_geometry(new_mesh, node_name="test", geom_name="test")

    scene.export(file_obj="temp/polygon.glb", file_type=".glb")


def test_instanced_mapped_geometry():
    bm = Beam("bm1", (0, 0, 0), (1, 0, 0))

    _ = bm.to_fem_obj(0.1, "line")
    # for el in fem.elements.lines:
    #     el.nodes


def test_vertex_coloring_advanced():
    vertices = np.load("../../files/fem_files/meshes/neutral/vertices.npy")
    faces = np.load("../../files/fem_files/meshes/neutral/faces.npy")
    vertex_color = np.load("../../files/fem_files/meshes/neutral/colors.npy")

    scene = trimesh.Scene()
    new_mesh = trimesh.Trimesh(
        vertices=vertices,
        faces=faces,
        vertex_colors=vertex_color,
    )

    scene.add_geometry(new_mesh, node_name="test", geom_name="test")
    os.makedirs("temp", exist_ok=True)
    scene.export(file_obj="temp/planes.glb", file_type=".glb")
