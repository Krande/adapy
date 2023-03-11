import xml.etree.ElementTree as ET

from ada.fem.formats.sesam.xml.read.read_beams import el_to_beam
from ada.fem.formats.sesam.xml.read.read_materials import get_materials
from ada.fem.formats.sesam.xml.read.read_sections import get_sections


def iter_beams_from_xml(xml_path):
    from ada import Part

    xml_root = ET.parse(str(xml_path)).getroot()
    all_beams = xml_root.findall(".//straight_beam") + xml_root.findall(".//curved_beam")
    p = Part("tmp")
    p._sections = get_sections(xml_root, p)
    p._materials = get_materials(xml_root, p)
    for bm_el in all_beams:
        yield from el_to_beam(bm_el, p)
