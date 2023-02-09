from __future__ import annotations

import os
import pathlib
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from .write_bcs import add_boundary_conditions
from .write_beams import add_beams
from .write_masses import add_masses
from .write_materials import add_materials
from .write_sat_embedded import embed_sat_geometry
from .write_sections import add_sections

if TYPE_CHECKING:
    from ada import Part

_XML_TEMPLATE = pathlib.Path(__file__).parent / "resources/xml_blank.xml"


def write_xml(part: Part, xml_file, embed_sat=False):
    if not isinstance(xml_file, pathlib.Path):
        xml_file = pathlib.Path(xml_file)

    tree = ET.parse(_XML_TEMPLATE)
    root = tree.getroot()

    part.consolidate_sections()
    part.consolidate_materials()

    # Find the <properties> element
    structure_domain = root.find("./model/structure_domain")
    structures_elem = ET.SubElement(structure_domain, "structures")
    properties = structure_domain.find("./properties")

    # Add Properties
    add_sections(properties, part)
    add_materials(properties, part)

    # Add SAT geometry (maybe only applicable for plate geometry)
    sat_map = dict()
    if embed_sat:
        sat_map = embed_sat_geometry(structure_domain, part)

    # Add structural elements
    add_beams(structures_elem, part, sat_map)
    add_boundary_conditions(structures_elem, part)
    add_masses(structures_elem, part)

    # Write the modified XML back to the file
    os.makedirs(xml_file.parent, exist_ok=True)
    tree.write(str(xml_file))
