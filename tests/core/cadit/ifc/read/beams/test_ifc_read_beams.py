import pytest

import ada


def test_read_standard_case_beams(example_files, ifc_test_dir):
    a = ada.from_ifc(example_files / "ifc_files/beams/beam-standard-case.ifc")

    a.to_ifc(ifc_test_dir / "beam-standard-case-re-exported.ifc")

    p = a.get_by_name("Building")
    assert len(p.beams) == 18

    bm_a1: ada.Beam = p.get_by_name("A-1")
    assert tuple(bm_a1.n1.p) == (0.0, 0.0, 0.0)
    assert tuple(bm_a1.n2.p) == (2.0, 0.0, 0.0)

    bm_a2: ada.Beam = p.get_by_name("A-2")
    assert tuple(bm_a2.n1.p) == (0.0, 1.5, 0.0)
    assert tuple(bm_a2.n2.p) == (2.0, 1.5, 0.0)

    bm_b1: ada.Beam = p.get_by_name("B-1")
    assert tuple(bm_b1.n1.p) == (0.0, 0.0, 1.5)
    assert tuple(bm_b1.n2.p) == pytest.approx((2.94, 0.243, 2.046), abs=1e-3)


def test_read_extruded_solid_beams(example_files, ifc_test_dir):
    a = ada.from_ifc(example_files / "ifc_files/beams/beam-extruded-solid.ifc")
    p = a.get_part("Grasshopper Building")
    assert len(p.beams) == 1
    bm = p.beams[0]

    assert tuple(bm.n1.p) == (0.0, 0.0, 0.0)
    assert tuple(bm.n2.p) == (0.0, 10.0, 0.0)


def test_read_varying_cardinal_points(example_files, ifc_test_dir):
    a = ada.from_ifc(example_files / "ifc_files/beams/beam-varying-cardinal-points.ifc")
    p = a.get_part("IfcBuilding")
    assert len(p.beams) == 4
    bm = p.beams[0]
    print(bm)
    # Todo: import and check the cardinal points


def test_read_varying_extrusion_path(example_files, ifc_test_dir):
    a = ada.from_ifc(example_files / "ifc_files/beams/beam-varying-extrusion-paths.ifc")
    _ = a.to_ifc(ifc_test_dir / "beam-varying-extrusion-paths.ifc", file_obj_only=True)
    print(a)


def test_read_revolved_solid(example_files, ifc_test_dir):
    a = ada.from_ifc(example_files / "ifc_files/beams/beam-revolved-solid.ifc")
    _ = a.to_ifc(ifc_test_dir / "beam-revolved-solid.ifc", file_obj_only=True)
    print(a)
