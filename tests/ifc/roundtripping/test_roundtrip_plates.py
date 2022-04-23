import ada


def test_roundtrip_plate(plate1, ifc_test_dir):
    ifc_beam_file = ifc_test_dir / "plate1.ifc"
    fp = (ada.Assembly() / (ada.Part("MyPart") / plate1)).to_ifc(ifc_beam_file, return_file_obj=True)

    a = ada.from_ifc(fp)
    pl: ada.Plate = a.get_by_name("MyPlate")
    p = pl.parent

    assert p.name == "MyPart"

    p.fem = pl.to_fem_obj(0.1, "shell")
    a.to_fem("MyFEM_plate_from_ifc_file", "usfos", overwrite=True)
