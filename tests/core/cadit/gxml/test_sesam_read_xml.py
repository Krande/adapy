import ada


def test_sesam_xml(example_files, tmp_path):
    xml_file = (example_files / "fem_files/sesam/curved_plates.xml").resolve().absolute()
    a = ada.from_genie_xml(xml_file)

    assert len(a.get_all_subparts()) == 1
    p = a.get_all_subparts()[0]
    a.to_ifc(tmp_path / "sesam_test.ifc", validate=True)
