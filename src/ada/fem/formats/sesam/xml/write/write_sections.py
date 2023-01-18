from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ada import Part, Section


def add_sections(root: ET.Element, part: Part):
    sections_elem = ET.Element("sections")

    # Add the new element underneath <properties>
    root.append(sections_elem)

    for section in part.sections:
        if section.type == section.TYPES.ANGULAR:
            add_angular_section(section, sections_elem)
        elif section.type == section.TYPES.TUBULAR:
            add_pipe_section(section, sections_elem)
        else:
            logging.error(f"The profile type {section.type} is not yet supported for Genie XML export")


def add_angular_section(section: Section, xml_root: ET.Element):
    section_elem = ET.Element("section", {"name": section.name, "description": ""})
    prop_map = dict(h="h", b="w_btn", tw="t_w", tf="t_fbtn")

    props = {key: str(getattr(section, value)) for key, value in prop_map.items()}
    props.update({"fabrication": "unknown", "sfy": "1", "sfz": "1", "general_properties_method": "computed"})
    section_props = ET.Element("l_section", props)

    section_elem.append(section_props)
    xml_root.append(section_elem)


def add_pipe_section(section: Section, xml_root: ET.Element):
    section_elem = ET.Element("section", {"name": section.name, "description": ""})
    prop_map = dict(h="h", b="w_btn", tw="t_w", tf="t_fbtn")

    props = {key: str(getattr(section, value)) for key, value in prop_map.items()}
    props.update({"fabrication": "unknown", "sfy": "1", "sfz": "1", "general_properties_method": "computed"})
    section_props = ET.Element("l_section", props)

    section_elem.append(section_props)
    xml_root.append(section_elem)


def add_i_section(section: Section, xml_root: ET.Element):
    ...


def add_box_section(section: Section, xml_root: ET.Element):
    # Create the <section> element
    section = ET.Element("section", {"name": "RHS_500x300x8", "description": "EN 10219-2: 1997 lib: RHS 500x300"})

    # Create the <box_section> element
    box_section = ET.Element(
        "box_section",
        {
            "h": "0.5",
            "b": "0.3",
            "tw": "0.008",
            "tftop": "0.008",
            "tfbot": "0.008",
            "fabrication": "unknown",
            "sfy": "1",
            "sfz": "1",
            "general_properties_method": "library",
        },
    )

    # Create the <libraryGeneralSection> element
    libraryGeneralSection = ET.Element(
        "libraryGeneralSection",
        {
            "area": "0.0125",
            "ix": "0.0004256",
            "iy": "0.0004373",
            "iz": "0.0001995",
            "iyz": "0",
            "wxmin": "0.002298624",
            "wymin": "0.001749",
            "wzmin": "0.00133",
            "shary": "0.004317384512",
            "sharz": "0.006673651527",
            "shceny": "0",
            "shcenz": "0",
            "sy": "0.001058912",
            "sz": "0.000745312",
            "wpy": "0.0021",
            "wpz": "0.00148",
        },
    )

    # Append the <libraryGeneralSection> element to the <box_section> element
    box_section.append(libraryGeneralSection)

    # Append the <box_section> element to the <section> element
    section.append(box_section)
    xml_root.append(section)
    # You can now append the <section> element to your xml tree


def add_unsymm_i_section(section: Section, xml_root: ET.Element):
    ...
