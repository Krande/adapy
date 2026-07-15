from __future__ import annotations

import pathlib
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING, Callable

from ...sat.write.writer import part_to_sat_writer
from .write_bcs import add_concept_constraints, add_fem_boundary_conditions
from .write_beams import add_beams
from .write_equipments import add_equipments
from .write_hinges import add_hinges
from .write_load_case import add_loads
from .write_masses import add_masses
from .write_materials import add_materials
from .write_plates import add_plates
from .write_sat_embedded import (
    embed_sat_geometry,
    sat_to_base64_segments,
    splice_cdata_segments,
)
from .write_sections import add_sections
from .write_sets import add_sets

if TYPE_CHECKING:
    from ada import Part

_XML_TEMPLATE = pathlib.Path(__file__).parent / "resources/xml_blank.xml"


def write_xml(part: Part, xml_file, embed_sat=False, writer_postprocessor: Callable[[ET.Element, Part], None] = None):
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
    add_hinges(properties, part)

    # Build the ACIS body up front: add_plates needs its plate -> FACE name map
    # to emit each <sheet>'s <sat_reference>.
    sw = part_to_sat_writer(part) if embed_sat else None

    # Add structural elements
    add_beams(structures_elem, part, sw)
    add_plates(structure_domain, part, sw)
    add_fem_boundary_conditions(structures_elem, part)
    add_masses(structures_elem, part)

    add_sets(structure_domain, part)

    # add loads
    add_loads(root, part)
    add_concept_constraints(structures_elem, part)
    add_equipments(root, part)

    if writer_postprocessor:
        writer_postprocessor(root, part)

    xml_file.parent.mkdir(exist_ok=True, parents=True)
    # A model with no plates has no ACIS body, so there is nothing to embed.
    if not embed_sat or sw.is_empty:
        tree.write(str(xml_file), encoding="utf-8")
        return

    # <geometry> goes last, after <sets>, matching Genie's own export.
    segments = sat_to_base64_segments(sw.to_str())
    embed_sat_geometry(structure_domain, len(segments))

    xml_str = ET.tostring(tree.getroot(), encoding="unicode")
    xml_str = splice_cdata_segments(xml_str, segments)
    with open(xml_file, "w", encoding="utf-8") as file:
        file.write(xml_str)
