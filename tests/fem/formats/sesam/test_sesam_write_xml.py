import ada
from ada.fem.formats.sesam.xml.write.write_xml import write_xml


def test_roundtrip_xml(fem_files):
    original_xml_file = fem_files / "sesam/xml_all_basic_props.xml"
    new_xml = "temp/basic_props.xml"
    a = ada.from_genie_xml(original_xml_file)

    write_xml(a, new_xml)
    # Todo: return file object -> Pass that in to ada genie xml reader and compare the two assemblies.


def test_create_sesam_xml_from_mixed(mixed_model):
    xml_file = "temp/xml_model.xml"

    write_xml(mixed_model, xml_file)
