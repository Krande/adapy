import ada


def test_export_ifc_plate(plate1, ifc_test_dir):
    _ = (ada.Assembly() / (ada.Part("MyPart") / plate1)).to_ifc(ifc_test_dir / "exported_plate.ifc", file_obj_only=True)
