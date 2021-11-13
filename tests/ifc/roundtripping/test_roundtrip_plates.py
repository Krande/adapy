import ada


def test_roundtrip_ipe_beam(plate1, ifc_test_dir):
    ifc_beam_file = ifc_test_dir / "ipe300.ifc"
    (ada.Assembly() / (ada.Part("MyPart") / plate1)).to_ifc(ifc_beam_file)

    a = ada.from_ifc(ifc_beam_file)
    bm: ada.Beam = a.get_by_name("MyIPE300")
    p = bm.parent
    sec = bm.section

    assert p.name == "MyPart"
    assert bm.name == "MyIPE300"
    assert sec.type == "IPE"

    # p.fem = bm.to_fem_obj(0.1, "shell")
    # a.to_fem("MyFEM_from_ifc_file", "usfos", overwrite=True)
