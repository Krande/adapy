import ada


def test_export_ifc_plate(plate1):
    _ = (ada.Assembly() / (ada.Part("MyPart") / plate1)).to_ifc(file_obj_only=True)
