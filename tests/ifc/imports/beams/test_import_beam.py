import ada


def test_import_ipe_beam(example_files):
    ifc_beam_file = example_files / "ifc_files/beams/ipe300.ifc"
    a = ada.from_ifc(ifc_beam_file)
    bm: ada.Beam = a.get_by_name("MyBeam")
    p = bm.parent
    sec = bm.section

    assert p.name == "MyPart"
    assert bm.name == "MyBeam"
    assert sec.type == "IPE"

    # p.fem = bm.to_fem_obj(0.1, "shell")
    # a.to_fem("MyFEM_from_ifc_file", "usfos")
