from ada.cadit.gxml.sat_helpers import get_sat_text_from_xml


def test_read_genie_single_beam_xml(example_files):
    xml_file = (example_files / "fem_files/sesam/single_beam.xml").resolve().absolute()
    _ = get_sat_text_from_xml(xml_file)
    # print(_)


def test_read_genie_beams_xml(example_files):
    xml_file = (example_files / "fem_files/sesam/xml_all_basic_props.xml").resolve().absolute()
    _ = get_sat_text_from_xml(xml_file)
    # print(_)


def test_read_genie_flat_plate_xml(example_files):
    xml_file = (example_files / "fem_files/sesam/plate_flat.xml").resolve().absolute()
    _ = get_sat_text_from_xml(xml_file)


def test_read_genie_curved_plate_xml(example_files):
    xml_file = (example_files / "fem_files/sesam/curved_plates.xml").resolve().absolute()
    _ = get_sat_text_from_xml(xml_file)
    # print(_)
