import pathlib
import xml.etree.ElementTree as ET

from ada import Part
from ada.fem.formats.sesam.xml.read.read_beams import el_to_beam
from ada.fem.formats.sesam.xml.read.read_materials import get_materials
from ada.fem.formats.sesam.xml.read.read_sections import get_sections


class GxmlStore:
    def __init__(self, xml_path: pathlib.Path):
        self.xml_root = ET.parse(str(xml_path)).getroot()
        self.p = Part("tmp")
        p = self.p
        p._sections = get_sections(self.xml_root, p)
        p._materials = get_materials(self.xml_root, p)

    def iter_geometry_from_xml(self):
        yield from self.iter_beams_from_xml()
        yield from self.iter_plates_from_xml()

    def iter_beams_from_xml(self):
        p = self.p

        for bm in self.xml_root.iterfind(".//straight_beam"):
            yield from el_to_beam(bm, p)

        for curved_bm in self.xml_root.iterfind(".//curved_beam"):
            yield from el_to_beam(curved_bm, p)

    def iter_plates_from_xml(self):
        from ada.fem.formats.sesam.xml.read.read_plates import iter_plates

        yield from iter_plates(self.xml_root, self.p)
