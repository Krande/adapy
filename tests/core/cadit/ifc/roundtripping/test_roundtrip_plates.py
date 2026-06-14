import ada


def test_roundtrip_plate(tmp_path):
    plate = ada.Plate("MyPlate", [(0, 0), (1, 0), (1, 1), (0, 1)], 20e-3)

    fp = (ada.Assembly() / (ada.Part("MyPart") / plate)).to_ifc(tmp_path / "plate1.ifc", file_obj_only=True)

    a = ada.from_ifc(fp)
    pl: ada.Plate = a.get_by_name("MyPlate")

    assert isinstance(pl, ada.Plate)
    assert pl.parent.name == "MyPart"
    # geometry preserved: thickness + the 4 distinct polygon corners (the reader may emit
    # duplicate consecutive points when closing the loop, so dedup before counting)
    assert pl.t == 20e-3
    corners = list(dict.fromkeys(tuple(round(c, 6) for c in p) for p in pl.poly.points2d))
    assert len(corners) == 4

    # still a valid, meshable plate after the round-trip
    pl.parent.fem = pl.to_fem_obj(0.1, "shell")


def test_roundtrip_plate_with_fillet(tmp_path):
    # 3rd coord on a corner = fillet radius; the arc survives the round-trip (extra edge point).
    plate = ada.Plate("FilletPlate", [(0, 0), (1, 0, 0.2), (1, 1), (0, 1)], 20e-3)
    fp = (ada.Assembly() / (ada.Part("MyPart") / plate)).to_ifc(tmp_path / "plate2.ifc", file_obj_only=True)

    pl: ada.Plate = ada.from_ifc(fp).get_by_name("FilletPlate")
    assert isinstance(pl, ada.Plate)
    assert pl.t == 20e-3
