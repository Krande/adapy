import pytest

import ada
from ada.fem.meshing import GmshOptions, GmshSession


@pytest.fixture
def plate() -> ada.Plate:
    return ada.Plate("pl1", [(0, 0), (1, 0), (1, 1), (0, 1)], 10e-3)


def test_quad_meshed_plate(plate):
    with GmshSession(silent=True) as gs:
        gs.add_obj(plate, "shell")
        gs.mesh(0.1, use_quads=True)
        fem = gs.get_fem()

    el_types = {el_type.value: list(group) for el_type, group in fem.elements.group_by_type()}

    assert len(el_types.keys()) == 1
    assert len(el_types["QUAD"]) == 100

    fem.options.ABAQUS.default_elements.SHELL.QUAD = "S4"
    # (ada.Assembly() / (ada.Part("MyPart", fem=fem) / plate)).to_fem(
    #     "Quad_ufo", "usfos", overwrite=True, scratch_dir=test_meshing_dir
    # )


def test_quad_quadratic_meshed_plate(plate):
    with GmshSession(silent=True, options=GmshOptions(Mesh_ElementOrder=2)) as gs:
        gs.add_obj(plate, "shell")
        gs.mesh(0.1, use_quads=True)
        fem = gs.get_fem()

    fem.options.ABAQUS.default_elements.SHELL.QUAD8 = "S8R"

    el_types = {el_type.value: list(group) for el_type, group in fem.elements.group_by_type()}

    assert len(el_types.keys()) == 1
    assert len(el_types["QUAD8"]) == 100

    # (ada.Assembly() / (ada.Part("MyPart", fem=fem) / plate)).to_fem(
    #     "Quad8Mesh_plate_aba", "abaqus", overwrite=True, scratch_dir=test_meshing_dir
    # )


def test_quad_meshed_beam():
    bm = ada.Beam("pl1", (0, 0, 0), (1, 0, 0), "IPE400")

    with GmshSession(silent=True) as gs:
        gs.add_obj(bm, "shell")
        gs.mesh(0.1, use_quads=True)
        fem = gs.get_fem()

    el_types = {el_type.value: list(group) for el_type, group in fem.elements.group_by_type()}

    assert len(el_types.keys()) == 1
    assert len(el_types["QUAD"]) == 120

    # (ada.Assembly() / (ada.Part("MyPart", fem=fem) / bm)).to_fem(
    #     "QuadMesh_beam_aba", "abaqus", overwrite=True, scratch_dir=test_meshing_dir
    # )


def test_quad_meshed_plate_with_hole():
    pl = ada.Plate("pl1", [(0, 0), (1, 0), (1, 1), (0, 1)], 10e-3)
    pl.add_boolean(ada.PrimCyl("Mycyl", (0.5, 0.5, -0.5), (0.5, 0.5, 0.5), 0.2))

    with GmshSession(options=GmshOptions(Mesh_ElementOrder=1), silent=True) as gs:
        gs.add_obj(pl, "shell")
        gs.mesh(0.1, use_quads=True)
        fem = gs.get_fem()

    el_types = {el_type.value: list(group) for el_type, group in fem.elements.group_by_type()}

    assert len(el_types.keys()) == 1
    # Tolerance, not an exact count: the boolean cylinder cut leaves a curved
    # boundary whose discretisation depends on floating-point rounding, so gmsh
    # returns a slightly different element count per architecture -- 114 on
    # x86_64, 116 on arm64. The three meshes above have straight boundaries and
    # are stable at an exact count; only this one is sensitive. Same treatment
    # (and same reason) as test_mesh_combined_fem.py's TRIANGLE assertion.
    #
    # The assertion still earns its place: it catches a mesh that collapses or
    # doubles, which is what a real regression here would look like.
    assert len(el_types["QUAD"]) == pytest.approx(114, abs=10)

    # (ada.Assembly() / (ada.Part("MyPart", fem=fem) / pl)).to_fem(
    #     "QuadMesh_w_pen_ufo", "usfos", overwrite=True, scratch_dir=test_meshing_dir
    # )
