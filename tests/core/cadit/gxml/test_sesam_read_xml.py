import ada


def test_sesam_xml(example_files):
    xml_file = (example_files / "fem_files/sesam/curved_plates.xml").resolve().absolute()
    _ = ada.from_genie_xml(xml_file)
