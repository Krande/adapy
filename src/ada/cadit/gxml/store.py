import pathlib
import xml.etree.ElementTree as ET

from ada import Part
from ada.cadit.gxml.read.helpers import (
    apply_mass_density_factors,
    yield_plate_elems_to_plate,
)
from ada.cadit.gxml.read.read_bcs import get_boundary_conditions
from ada.cadit.gxml.read.read_beams import el_to_beam
from ada.cadit.gxml.read.read_joints import get_joints
from ada.cadit.gxml.read.read_masses import get_masses
from ada.cadit.gxml.read.read_materials import get_materials
from ada.cadit.gxml.read.read_sections import get_sections
from ada.cadit.gxml.read.read_sets import get_sets
from ada.cadit.gxml.sat_helpers import write_xml_sat_text_to_file
from ada.cadit.sat.store import SatReaderFactory
from ada.config import Config, logger


class GxmlStore:
    def __init__(self, xml_path: pathlib.Path):
        if isinstance(xml_path, str):
            xml_path = pathlib.Path(xml_path).resolve().absolute()

        self.sat_file = xml_path.with_suffix(".sat")

        if not self.sat_file.exists():
            logger.info("SAT file does not exist. Creating SAT file")
            write_xml_sat_text_to_file(xml_file=xml_path, out_file=self.sat_file)
        elif self.sat_file.exists() and self.sat_file.lstat().st_ctime < xml_path.lstat().st_ctime:
            logger.info("XML file is newer than SAT file. Updating SAT file")
            write_xml_sat_text_to_file(xml_file=xml_path, out_file=self.sat_file)

        self.xml_root = ET.parse(str(xml_path)).getroot()
        self.sat_factory = SatReaderFactory(self.sat_file)

        model = self.xml_root.find(".//model")
        p = Part(model.attrib["name"])
        self.p = p
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

    def iter_plate_shell_elem(self):
        for fp in self.xml_root.iterfind(".//flat_plate"):
            yield fp

        for fp in self.xml_root.iterfind(".//curved_shell"):
            yield fp

    def iter_plates_from_xml(self):
        sat_d = {name: points for name, points in self.sat_factory.iter_flat_plates()}
        if Config().gxml_import_advanced_faces is True:
            sat_faces = {name: geom for name, geom in self.sat_factory.iter_curved_face()}
            sat_d.update(sat_faces)

        thick_map = dict()
        for thickn in self.xml_root.iterfind(".//thickness"):
            res = thickn.find(".//constant_thickness")
            thick_map[thickn.attrib["name"]] = float(res.attrib["th"])

        for fp in self.xml_root.iterfind(".//flat_plate"):
            yield from yield_plate_elems_to_plate(fp, self.p, sat_d, thick_map)

        for fp in self.xml_root.iterfind(".//curved_shell"):
            yield from yield_plate_elems_to_plate(fp, self.p, sat_d, thick_map)

    def to_part(self, extract_joints=False) -> Part:
        from ada.api.containers import Beams, Plates

        p = self.p
        p._plates = Plates(self.iter_plates_from_xml(), parent=p)
        p._beams = Beams(self.iter_beams_from_xml(), parent=p)

        for bm in p.beams:
            p.nodes.add(bm.n1)
            p.nodes.add(bm.n2)
        p._groups = get_sets(self.xml_root, p)
        if extract_joints is True:
            p._connections = get_joints(self.xml_root, p)

        get_boundary_conditions(self.xml_root, p)
        get_masses(self.xml_root, p)

        all_plates = len(p.plates)
        all_beams = len(p.beams)
        all_joints = len(p.connections)

        apply_mass_density_factors(self.xml_root, p)

        print(f"Finished importing Genie XML (beams={all_beams}, plates={all_plates}, joints={all_joints})")
        return p
