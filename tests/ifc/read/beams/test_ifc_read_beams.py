import ada


def test_read_standard_case_beams(example_files, ifc_test_dir):
    a = ada.from_ifc(example_files / "ifc_files/beams/beam-standard-case.ifc")
    p = a.get_part("Building")
    assert len(p.beams) == 18

    bm_a1: ada.Beam = p.get_by_name("A-1")
    assert tuple(bm_a1.n1.p) == (0.0, 0.0, 0.0)
    assert tuple(bm_a1.n2.p) == (2.0, 0.0, 0.0)

    # bm_b1: ada.Beam = p.get_by_name("B-1")
    # assert tuple(bm_a1.n1.p) == (0.0, 0.0, 0.0)
    # assert tuple(bm_a1.n2.p) == (2.0, 0.0, 0.0)
    # print(bm_a1)

    # a.to_ifc(ifc_test_dir / "beam-standard-case-re-exported.ifc")


def test_read_extruded_solid_beams(example_files, ifc_test_dir):
    a = ada.from_ifc(example_files / "ifc_files/beams/beam-extruded-solid.ifc")
    p = a.get_part("Grasshopper Building")
    assert len(p.beams) == 1
    bm = p.beams[0]
    print(bm)


def test_read_varying_cardinal_points(example_files, ifc_test_dir):
    a = ada.from_ifc(example_files / "ifc_files/beams/beam-varying-cardinal-points.ifc")
    p = a.get_part("IfcBuilding")
    assert len(p.beams) == 4
    bm = p.beams[0]
    print(bm)


def test_read_varying_extrusion_path(example_files, ifc_test_dir):
    a = ada.from_ifc(example_files / "ifc_files/beams/beam-varying-extrusion-paths.ifc")
    print(a)


def test_read_revolved_solid(example_files, ifc_test_dir):
    a = ada.from_ifc(example_files / "ifc_files/beams/beam-revolved-solid.ifc")
    print(a)
