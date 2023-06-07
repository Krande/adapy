import os

import numpy as np
import trimesh
from trimesh.path.entities import Line
from trimesh.visual.material import PBRMaterial

from ada import Beam
from ada.core.vector_utils import unit_vector
from ada.core.vector_transforms import rot_matrix


def test_vertex_coloring_simple(polygon_mesh):
    scene = trimesh.Scene()

    assert polygon_mesh.visual.kind == "vertex"

    scene.add_geometry(polygon_mesh, node_name="test", geom_name="test")

    os.makedirs("temp", exist_ok=True)
    scene.export(file_obj="temp/polygon2.glb", file_type=".glb")


def test_instanced_mapped_geometry():
    bm = Beam("bm1", (0, 0, 0), (1, 0, 0), sec="IPE300")
    obj_mesh = bm.to_obj_mesh()
    scale_vector = bm.xvec * 0.1
    scale_vector[scale_vector == 0.0] = 1.0

    base_mesh = obj_mesh.to_trimesh()[0]
    base_mesh.apply_scale(scale_vector.astype(float))
    scene = trimesh.Scene()

    fem = bm.to_fem_obj(0.1, "line")
    for el in fem.elements.lines:
        name = f"bm_el_{el.id}"
        n1, n2 = el.nodes[0].p, el.nodes[-1].p
        delta = n2 - n1

        vec = unit_vector(delta)
        x, y, z = n1
        m3x3 = rot_matrix(vec, bm.xvec)
        m3x3_with_col = np.append(m3x3, np.array([[x], [y], [z]]), axis=1)
        m4x4 = np.r_[m3x3_with_col, [np.array([0, 0, 0, 1])]]

        scene.add_geometry(base_mesh, name, name, transform=m4x4)

    # rotate scene before exporting
    m3x3 = rot_matrix((0, -1, 0))
    m3x3_with_col = np.append(m3x3, np.array([[0], [0], [0]]), axis=1)
    m4x4 = np.r_[m3x3_with_col, [np.array([0, 0, 0, 1])]]
    scene.apply_transform(m4x4)

    scene.export(file_obj="temp/mapped_instances.glb", file_type=".glb")


def test_vertex_coloring_advanced(root_dir):
    neutral_dir = root_dir / "files/fem_files/numpy_files/simple_stru_eig1"
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
