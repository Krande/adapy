import ada


def test_roundtrip_xml(fem_files, tmp_path):
    original_xml_file = fem_files / "sesam/xml_all_basic_props.xml"
    new_xml = tmp_path / "basic_props.xml"

    a = ada.from_genie_xml(original_xml_file)
    a.to_genie_xml(new_xml)


def test_create_sesam_xml_from_mixed(mixed_model, tmp_path):
    xml_file = tmp_path / "mixed_xml_model.xml"

    mixed_model.to_genie_xml(xml_file)
