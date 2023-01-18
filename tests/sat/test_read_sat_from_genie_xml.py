from ada.fem.formats.sesam.xml.read.read_plates import get_sat_text_from_xml


def test_read_genie_xml(example_files):
    xml_file = (example_files / "fem_files/sesam/plate_flat.xml").resolve().absolute()
    get_sat_text_from_xml(xml_file)
