import ada
import xml.etree.ElementTree as ET

def test_roundtrip_xml(fem_files, tmp_path):
    original_xml_file = fem_files / "sesam/xml_all_basic_props.xml"
    new_xml = tmp_path / "basic_props.xml"

    a = ada.from_genie_xml(original_xml_file)
    a.to_genie_xml(new_xml)


def test_create_sesam_xml_from_mixed(mixed_model, tmp_path):
    xml_file = tmp_path / "mixed_xml_model.xml"

    mixed_model.to_genie_xml(xml_file)


def test_create_sesam_xml_with_plate(tmp_path):
    pl = ada.Plate("pl", [(0, 0), (10, 0), (10, 10), (0, 10)], 0.1)
    a = ada.Assembly("a") / pl
    a.to_genie_xml(tmp_path / "plate.xml", embed_sat=True)

def test_create_groups_split_across_parts(tmp_path):
    p1 = ada.Part('P1') / (ada.Beam("bm1", (0,0,0), (1,0,0), 'IPE200'))
    p2 = ada.Part("P2") / (ada.Beam("bm2", (0,0,1), (1,0,1), 'IPE200'))
    p1.add_group("group1", [p1.beams[0]])
    p2.add_group("group1", [p2.beams[0]])

    a = ada.Assembly("a") / (p1 , p2)

    dest = tmp_path / "groups_split_across_parts.xml"

    a.to_genie_xml(dest, embed_sat=False)

    tree = ET.parse(dest)
    root = tree.getroot()
    sets = root.find("./model/structure_domain/sets")

    assert sets is not None

    assert len(sets.findall("./set")) == 1

    assert len(sets.findall("./set/concepts/concept")) == 2
    assert sets.find("./set/concepts/concept[@concept_ref='bm1']") is not None
    assert sets.find("./set/concepts/concept[@concept_ref='bm2']") is not None


