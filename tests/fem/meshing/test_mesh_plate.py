import pytest

import ada
from ada.core.alignment_utils import align_to_plate


@pytest.fixture
def pl1():
    place1 = dict(placement=ada.Placement(origin=(0, 0, 0), xdir=(1, 0, 0), zdir=(0, 0, 1)))
    return ada.Plate("MyPl", [(0, 0, 0.2), (5, 0, 0.2), (5, 5), (0, 5)], 20e-3, **place1)


@pytest.fixture
def pl2():
    place2 = dict(placement=ada.Placement(origin=(1, 0, -0.1), xdir=(0, 0, 1), zdir=(-1, 0, 0)))
    return ada.Plate("MyPl2", [(0, 0, 0.2), (5, 0, 0.2), (5, 5), (0, 5)], 20e-3, **place2)


def test_basic_plate(pl1, test_meshing_dir):
    p = ada.Part("MyFem") / pl1
    p.fem = pl1.to_fem_obj(5, "shell")
    assert len(p.fem.elements) == 8
    assert len(p.fem.nodes) == 9

    # (ada.Assembly("Test") / p).to_ifc(test_meshing_dir / "ADA_pl_mesh_ifc", include_fem=False)


def test_plate_mesh(pl1, pl2):
    points = [(1, 1, 0.2), (2, 1, 0.2), (2, 2, 0.2), (1, 2, 0.2)]
    pl1.add_penetration(ada.PrimExtrude("poly_extrude", points, **align_to_plate(pl1)))
    pl1.add_penetration(ada.PrimExtrude("poly_extrude2", points, **align_to_plate(pl2)))

    p = ada.Part("MyFem") / [pl1, pl2]
    p.fem = pl1.to_fem_obj(1, "shell")
    p.fem += pl2.to_fem_obj(1, "shell")

    assert len(p.fem.elements) == 236
    assert len(p.fem.nodes) == 153

    # (ada.Assembly("Test") / p).to_ifc(test_dir / "ADA_pl_w_holes_mesh_ifc", include_fem=True)
    # (ada.Assembly("Test") / p).to_fem("ADA_pl_mesh", "abaqus", scratch_dir=test_folder, overwrite=True)
