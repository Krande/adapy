import pytest

import ada
from ada.fem.meshing import GmshSession
from ada.param_models.utils import beams_along_polyline
from ada.visualize.femviz import get_edges_from_fem, get_faces_from_fem


@pytest.fixture
def bm_fem():
    bm = ada.Beam("bm1", n1=[0, 0, 0], n2=[2, 0, 0], sec="IPE220", colour="red")
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
    a.to_gltf("temp/sesam_1el_sh.glb")


def test_double_ses_elem(fem_files):
    a = ada.from_fem(fem_files / "sesam/2EL_SHELL_R1.SIF")
    a.to_gltf("temp/sesam_2el_sh.glb")


def test_bm_fem():
    bm = ada.Beam("bm1", n1=[0, 0, 0], n2=[1, 0, 0], sec="IPE220", colour="red")
    p = ada.Part("MyBmFEM")
    p.fem = bm.to_fem_obj(0.5, "shell")
    (ada.Assembly() / p).to_gltf("temp/bm.glb")


def test_mix_fem():
    bm = ada.Beam("bm1", n1=[0, 0, 0], n2=[2, 0, 0], sec="IPE220", colour="red")
    poly = [(0, 0, 0), (1, 0, 0), (1, 1, 0)]  # , (0, 1, 0)]

    objects = beams_along_polyline(poly, bm)
    objects += [ada.Plate("pl1", poly, 0.01)]

    a = ada.Assembly() / (ada.Part("BeamFEM") / objects)
    part = a.get_part("BeamFEM")
    p = ada.Part("FEMOnly")
    p.fem = part.to_fem_obj(0.5)
    mix_fem = ada.Assembly() / p

    # mix_fem.to_fem("mixed-fem", "usfos", "temp", overwrite=True)
    mix_fem.to_gltf("temp/mix_fem.glb")
