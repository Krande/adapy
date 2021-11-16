import pytest

import ada
from ada.fem.meshing import GmshOptions, GmshSession


@pytest.fixture
def plate() -> ada.Plate:
    return ada.Plate("pl1", [(0, 0), (1, 0), (1, 1), (0, 1)], 10e-3)


def test_quad_meshed_plate(plate, test_meshing_dir):
    with GmshSession(silent=True) as gs:
        gs.add_obj(plate, "shell")
        gs.mesh(0.1, use_quads=True)
        fem = gs.get_fem()

    el_types = {el_type: list(group) for el_type, group in fem.elements.group_by_type()}

    assert len(el_types.keys()) == 1
    assert len(el_types["QUAD"]) == 100

    fem.options.ABAQUS.default_elements.SHELL.QUAD = "S4"
    # (ada.Assembly() / (ada.Part("MyPart", fem=fem) / plate)).to_fem(
    #     "Quad_ufo", "usfos", overwrite=True, scratch_dir=test_meshing_dir
    # )


def test_quad_quadratic_meshed_plate(plate, test_meshing_dir):
    with GmshSession(silent=True, options=GmshOptions(Mesh_ElementOrder=2)) as gs:
        gs.add_obj(plate, "shell")
        gs.mesh(0.1, use_quads=True)
        fem = gs.get_fem()

    fem.options.ABAQUS.default_elements.SHELL.QUAD8 = "S8R"

    el_types = {el_type: list(group) for el_type, group in fem.elements.group_by_type()}

    assert len(el_types.keys()) == 1
    assert len(el_types["QUAD8"]) == 100

    # (ada.Assembly() / (ada.Part("MyPart", fem=fem) / plate)).to_fem(
    #     "Quad8Mesh_plate_aba", "abaqus", overwrite=True, scratch_dir=test_meshing_dir
    # )


def test_quad_meshed_beam(test_meshing_dir):
    bm = ada.Beam("pl1", (0, 0, 0), (1, 0, 0), "IPE400")

    with GmshSession(silent=True) as gs:
        gs.add_obj(bm, "shell")
        gs.mesh(0.1, use_quads=True)
        fem = gs.get_fem()

    el_types = {el_type: list(group) for el_type, group in fem.elements.group_by_type()}

    assert len(el_types.keys()) == 1
    assert len(el_types["QUAD"]) == 120

    # (ada.Assembly() / (ada.Part("MyPart", fem=fem) / bm)).to_fem(
    #     "QuadMesh_beam_aba", "abaqus", overwrite=True, scratch_dir=test_meshing_dir
    # )


def test_quad_meshed_plate_with_hole(test_meshing_dir):
    pl = ada.Plate("pl1", [(0, 0), (1, 0), (1, 1), (0, 1)], 10e-3)
    pl.add_penetration(ada.PrimCyl("Mycyl", (0.5, 0.5, -0.5), (0.5, 0.5, 0.5), 0.2))

    with GmshSession(options=GmshOptions(Mesh_ElementOrder=1), silent=True) as gs:
        gs.add_obj(pl, "shell")
        gs.mesh(0.1, use_quads=True)
        fem = gs.get_fem()

    el_types = {el_type: list(group) for el_type, group in fem.elements.group_by_type()}

    assert len(el_types.keys()) == 1
    assert len(el_types["QUAD"]) == 117

    # (ada.Assembly() / (ada.Part("MyPart", fem=fem) / pl)).to_fem(
    #     "QuadMesh_w_pen_ufo", "usfos", overwrite=True, scratch_dir=test_meshing_dir
    # )
