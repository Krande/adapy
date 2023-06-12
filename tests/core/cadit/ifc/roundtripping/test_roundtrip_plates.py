import ada


def test_roundtrip_plate(ifc_test_dir):
    plate = ada.Plate("MyPlate", [(0, 0), (1, 0, 0.2), (1, 1), (0, 1)], 20e-3)

    fp = (ada.Assembly() / (ada.Part("MyPart") / plate)).to_ifc(ifc_test_dir / "plate1.ifc", file_obj_only=True)

    a = ada.from_ifc(fp)
    pl: ada.Plate = a.get_by_name("MyPlate")
    p = pl.parent

    assert p.name == "MyPart"

    p.fem = pl.to_fem_obj(0.1, "shell")
    # a.to_fem("MyFEM_plate_from_ifc_file", "usfos", overwrite=True)
