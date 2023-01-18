from __future__ import annotations

import os
import pathlib
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from .write_sections import add_sections

if TYPE_CHECKING:
    from ada import Part

_XML_TEMPLATE = pathlib.Path(__file__).parent / "resources/xml_blank.xml"


def write_xml(part: Part, xml_file):
    if not isinstance(xml_file, pathlib.Path):
        xml_file = pathlib.Path(xml_file)

    tree = ET.parse(_XML_TEMPLATE)
    root = tree.getroot()

    part.consolidate_sections()
    part.consolidate_materials()

    # Find the <properties> element
    properties = root.find("./model/structure_domain/properties")

    # Create a new element to add
    add_sections(properties, part)

    # Write the modified XML back to the file
    os.makedirs(xml_file.parent, exist_ok=True)
    tree.write(str(xml_file))
