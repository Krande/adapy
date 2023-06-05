import pytest

import ada
from ada.core.alignment_utils import align_to_plate


def test_plate_mesh_from_2_fem(pl1, pl2, test_dir):
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

    # a.to_ifc(test_dir / "ADA_pl_w_holes_mesh.ifc", include_fem=False)
    # a.to_gltf(test_dir / "ADA_pl_w_holes_mesh.glb")
    # a.to_stp(test_dir / "ADA_pl_w_holes_mesh.step")
    # a.to_fem("ADA_pl_mesh", "code_aster", scratch_dir=test_dir, overwrite=True)

    assert len(el_types.keys()) == 1
    assert len(el_types["TRIANGLE"]) == pytest.approx(276, abs=15)
    assert len(p.fem.nodes) == pytest.approx(174, abs=10)

