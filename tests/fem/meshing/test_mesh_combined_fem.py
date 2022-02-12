import pytest

import ada
from ada.core.alignment_utils import align_to_plate


def test_plate_mesh_from_2_fem(pl1, pl2):
    points = [(1, 1, 0.2), (2, 1, 0.2), (2, 2, 0.2), (1, 2, 0.2)]
    pl1.add_penetration(ada.PrimExtrude("poly_extrude", points, **align_to_plate(pl1)))
    pl1.add_penetration(ada.PrimExtrude("poly_extrude2", points, **align_to_plate(pl2)))

    p = ada.Part("MyFem") / [pl1, pl2]
    p.fem = pl1.to_fem_obj(1, "shell")
    p.fem += pl2.to_fem_obj(1, "shell")

    el_types = {el_type: list(group) for el_type, group in p.fem.elements.group_by_type()}

    assert len(el_types.keys()) == 1
    assert len(el_types["TRIANGLE"]) == pytest.approx(236, abs=15)

    assert len(p.fem.nodes) == 153

    # (ada.Assembly("Test") / p).to_ifc(test_dir / "ADA_pl_w_holes_mesh_ifc", include_fem=True)
    # (ada.Assembly("Test") / p).to_fem("ADA_pl_mesh", "abaqus", scratch_dir=test_folder, overwrite=True)
