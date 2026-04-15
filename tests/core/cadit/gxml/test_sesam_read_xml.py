import ada


def test_sesam_xml(example_files, tmp_path, monkeypatch):
    xml_file = (example_files / "fem_files/sesam/curved_plates.xml").resolve().absolute()

    monkeypatch.setenv("ADA_GXML_IMPORT_ADVANCED_FACES", "true")
    a = ada.from_genie_xml(xml_file)

    assert len(a.get_all_subparts()) == 1
    p = a.get_all_subparts()[0]
    objects = list(p.get_all_physical_objects())

    # The fixture has 3 curved_shell elements. elev3 (4 flat split faces) is
    # merged into 1 Plate, elev4 is a single PlateCurved, and elev5 is a
    # PlateCurved plus 3 leftover flat faces.
    assert len(objects) == 6

    a.to_ifc(tmp_path / "sesam_test.ifc", validate=True)
