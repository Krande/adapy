import pytest

import ada
from ada.fem.meshing import GmshSession
from ada.param_models.utils import beams_along_polyline
from ada.visit.rendering.render_backend import SqLiteBackend
from ada.visit.utils import get_edges_from_fem, get_faces_from_fem


@pytest.fixture
def bm_fem():
    bm = ada.Beam("bm1", n1=[0, 0, 0], n2=[2, 0, 0], sec="IPE220", color="red")
    a = ada.Assembly() / (ada.Part("BeamFEM") / bm)
    part = a.get_part("BeamFEM")
    with GmshSession(silent=True) as gs:
        gs.add_obj(a.get_by_name("bm1"), geom_repr="line")
        gs.mesh(0.1)
        part.fem = gs.get_fem()
    return part


def test_beam_as_edges(bm_fem):
    assert len(bm_fem.fem.elements) == 20
    _ = get_edges_from_fem(bm_fem.fem)


def test_beam_as_faces(bm_fem):
    _ = get_faces_from_fem(bm_fem.fem)


def test_single_ses_elem(fem_files):
    a = ada.from_fem(fem_files / "sesam/1EL_SHELL_R1.SIF")
    # a.to_fem("usfos_fem", 'usfos', scratch_dir='temp')
    scene = a.to_trimesh_scene()

    # backend = SqLiteBackend('temp/sesam_1el_sh.db')
    backend = SqLiteBackend()
    tag = backend.add_metadata(scene.metadata, "sesam_1el_sh")
    backend.commit()

    res = backend.get_mesh_data_from_face_index(1, 0, tag)
    assert res.full_name == "EL1"
    res = backend.get_mesh_data_from_face_index(2, 0, tag)
    assert res.full_name == "EL1"
    # scene.to_gltf("temp/sesam_1el_sh.glb")


def test_double_ses_elem(fem_files):
    a = ada.from_fem(fem_files / "sesam/2EL_SHELL_R1.SIF")
    scene = a.to_trimesh_scene()

    # backend = SqLiteBackend('temp/sesam_2el_sh.db')
    backend = SqLiteBackend()
    tag = backend.add_metadata(scene.metadata, "sesam_2el_sh")
    backend.commit()

    res = backend.get_mesh_data_from_face_index(1, 0, tag)
    assert res.full_name == "EL1"

    res = backend.get_mesh_data_from_face_index(2, 0, tag)
    assert res.full_name == "EL1"

    res = backend.get_mesh_data_from_face_index(10, 0, tag)
    assert res.full_name == "EL2"

    # a.to_gltf("temp/sesam_2el_sh.glb")


def test_bm_fem():
    bm = ada.Beam("bm1", n1=[0, 0, 0], n2=[1, 0, 0], sec="IPE220", color="red")
    p = ada.Part("MyBmFEM")
    p.fem = bm.to_fem_obj(0.5, "shell")
    (ada.Assembly() / p).to_gltf("temp/bm.glb")


def test_mix_fem():
    bm = ada.Beam("bm1", n1=[0, 0, 0], n2=[2, 0, 0], sec="IPE220", color="red")
    poly = [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)]

    objects = beams_along_polyline(poly, bm)
    objects += [ada.Plate.from_3d_points("pl1", poly, 0.01)]

    a = ada.Assembly() / (ada.Part("BeamFEM") / objects)
    part = a.get_part("BeamFEM")
    p = ada.Part("FEMOnly")
    p.fem = part.to_fem_obj(0.5, interactive=False)
    mix_fem = ada.Assembly() / p

    mix_fem.to_fem("mixed-fem", "usfos", "temp", overwrite=True)
    # mix_fem.to_gltf("temp/mix_fem.glb")
