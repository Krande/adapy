import os

import numpy as np
import trimesh
from trimesh.visual.material import PBRMaterial
from ada import Beam


def test_vertex_coloring_simple():
    vertices = np.asarray([(0, 0, 0), (0, 1, 0), (1, 1, 0)], dtype="float32")
    faces = np.asarray([(0, 1, 2)], dtype="uint8")
    vertex_color = np.asarray([(245, 40, 145), (128, 50, 0), (200, 50, 0)], dtype="uint8")

    scene = trimesh.Scene()
    new_mesh = trimesh.Trimesh(vertices=vertices, faces=faces, vertex_colors=vertex_color)
    new_mesh.visual.material = PBRMaterial(doubleSided=True)

    if new_mesh.visual.kind != "vertex":
        raise ValueError("Vertex coloring is not applied")

    scene.add_geometry(new_mesh, node_name="test", geom_name="test")

    os.makedirs("temp", exist_ok=True)
    scene.export(file_obj="temp/polygon2.glb", file_type=".glb")


def test_instanced_mapped_geometry():
    bm = Beam("bm1", (0, 0, 0), (1, 0, 0), sec="IPE300")
    obj_mesh = bm.to_obj_mesh()

    fem = bm.to_fem_obj(0.1, "line")
    line_p = []
    paths = []
    for el in fem.elements.lines:
        n1, n2 = el.nodes[0], el.nodes[-1]
        line_p.append((n1, n2))
        # paths.append(trimesh.load_path(entities=line_p))

    scene = trimesh.Scene()
    new_mesh = obj_mesh.to_trimesh()
    # new_mesh.apply_scale(bm.xvec * 0.1)
    scene.add_geometry([new_mesh], bm.name, bm.name)
    scene.export(file_obj="temp/lines.glb", file_type=".glb")


def test_vertex_coloring_advanced():
    vertices = np.load("../../files/fem_files/meshes/neutral/vertices.npy")
    faces = np.load("../../files/fem_files/meshes/neutral/faces.npy")
    vertex_color = np.load("../../files/fem_files/meshes/neutral/colors.npy")

    scene = trimesh.Scene()
    new_mesh = trimesh.Trimesh(vertices=vertices, faces=faces, vertex_colors=vertex_color)
    new_mesh.visual.material = PBRMaterial(doubleSided=True)

    scene.add_geometry(new_mesh, node_name="test", geom_name="test")
    os.makedirs("temp", exist_ok=True)
    scene.export(file_obj="temp/planes.glb", file_type=".glb")


def test_line_segments():
    scene = trimesh.Scene()
    scene.add_geometry()
    scene.export(file_obj="temp/lines.glb", file_type=".glb")
