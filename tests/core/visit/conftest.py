import numpy as np
import pytest
import trimesh
from trimesh.visual.material import PBRMaterial

import ada
from ada.fem.meshing import GmshSession


@pytest.fixture
def visualization_test_dir(test_dir):
    return test_dir / "visualization"


@pytest.fixture
def polygon_mesh():
    vertices = np.asarray([(0, 0, 0), (0, 1, 0), (1, 1, 0)], dtype="float32")
    faces = np.asarray([(0, 1, 2)], dtype="uint8")
    vertex_color = np.asarray([(245, 40, 145), (128, 50, 0), (200, 50, 0)], dtype="uint8")
    new_mesh = trimesh.Trimesh(vertices=vertices, faces=faces, vertex_colors=vertex_color)
    new_mesh.visual.material = PBRMaterial(doubleSided=True)
    return new_mesh


@pytest.fixture
def bm_line_fem():
    bm = ada.Beam("bm1", n1=[0, 0, 0], n2=[2, 0, 0], sec="IPE220", color="red")
    a = ada.Assembly() / (ada.Part("BeamFEM") / bm)
    part = a.get_part("BeamFEMPart")
    with GmshSession(silent=True) as gs:
        gs.add_obj(a.get_by_name("bm1"), geom_repr="line")
        gs.mesh(0.1)
        part.fem = gs.get_fem()
    return part
