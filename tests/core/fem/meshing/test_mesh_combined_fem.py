import pytest

import ada
from ada.core.alignment_utils import align_to_plate


def test_plate_mesh_from_2_fem(pl1, pl2, tmp_path):
    points = [(1, 1, 0.2), (2, 1, 0.2), (2, 2, 0.2), (1, 2, 0.2)]
    pl1.color.opacity = 0.5
    pl2.color.opacity = 0.5

    bool1 = pl1.add_boolean(ada.PrimExtrude("poly_extrude", points, **align_to_plate(pl1)))
    bool2 = pl2.add_boolean(ada.PrimExtrude("poly_extrude2", points, **align_to_plate(pl2)))
    objects = [pl1, pl2]
    objects += [bool1.primitive, bool2.primitive]

    p = ada.Part("MyFem") / objects
    a = ada.Assembly("Test") / p
    assert len(a.parts) == 1

    p.fem = pl1.to_fem_obj(1, "shell")
    p.fem += pl2.to_fem_obj(1, "shell")

    el_types = {el_type.value: list(group) for el_type, group in p.fem.elements.group_by_type()}

    # a.to_ifc(tmp_path / "ADA_pl_w_holes_mesh.ifc", include_fem=False)
    # a.to_gltf(tmp_path / "ADA_pl_w_holes_mesh.glb")
    # a.to_stp(tmp_path / "ADA_pl_w_holes_mesh.step")
    # a.to_fem("ADA_pl_mesh", "code_aster", scratch_dir=tmp_path, overwrite=True)

    assert len(el_types.keys()) == 1
    assert len(el_types["TRIANGLE"]) == pytest.approx(276, abs=15)
    assert len(p.fem.nodes) == pytest.approx(174, abs=10)


def test_plate_offset():
    pl1 = ada.Plate("pl1", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01, orientation=ada.Placement())
    pl2 = ada.Plate("pl2", [(0, 0), (1, 0), (1, 1), (0, 1)], 0.01, orientation=ada.Placement((0, 0, 1)))
    p = ada.Part("MyFem") / [pl1, pl2]
    fem = p.to_fem_obj(1, "shell", interactive=False)
    assert len(fem.nodes) == 8
    assert len(fem.elements) == 4


def test_plates_perpendicular():
    p1x1 = [(0, 0), (1, 0), (1, 1), (0, 1)]
    pl1_5 = ada.Plate("pl1_5", p1x1, 0.01, orientation=ada.Placement((0, 0, 0.5)))
    pl3 = ada.Plate("pl3", p1x1, 0.01, orientation=ada.Placement(xdir=(1, 0, 0), zdir=(0, -1, 0)))

    p = ada.Part("MyFem") / [pl1_5, pl3]
    fem = p.to_fem_obj(1, "shell", interactive=False)
    assert len(fem.nodes) == 8
    assert len(fem.elements) == 6


def test_double_plates_perpendicular():
    p1x1 = [(0, 0), (1, 0), (1, 1), (0, 1)]
    pl1_25 = ada.Plate("pl1_25", p1x1, 0.01, orientation=ada.Placement((0, 0, 0.25)))
    pl1_75 = ada.Plate("pl1_75", p1x1, 0.01, orientation=ada.Placement((0, 0, 0.75)))
    pl3 = ada.Plate("pl3", p1x1, 0.01, orientation=ada.Placement(xdir=(1, 0, 0), zdir=(0, -1, 0)))

    p = ada.Part("MyFem") / [pl1_25, pl1_75, pl3]
    fem = p.to_fem_obj(1, "shell", interactive=False)
    assert len(fem.nodes) == 12
    assert len(fem.elements) == 10


def test_plate_offset_perpendicular():
    p1x1 = [(0, 0), (1, 0), (1, 1), (0, 1)]
    pl1 = ada.Plate("pl1", p1x1, 0.01, orientation=ada.Placement())
    pl1_5 = ada.Plate("pl1_5", p1x1, 0.01, orientation=ada.Placement((0, 0, 0.5)))
    pl2 = ada.Plate("pl2", p1x1, 0.01, orientation=ada.Placement((0, 0, 1)))
    pl3 = ada.Plate("pl3", p1x1, 0.01, orientation=ada.Placement(xdir=(1, 0, 0), zdir=(0, -1, 0)))
    pl4 = ada.Plate("pl4", p1x1, 0.01, orientation=ada.Placement(xdir=(0, 1, 0), zdir=(1, 0, 0)))

    p = ada.Part("MyFem") / [pl1, pl1_5, pl2, pl3, pl4]
    fem = p.to_fem_obj(1, "shell", interactive=False)
    assert len(fem.nodes) == 12
    assert len(fem.elements) == 14
