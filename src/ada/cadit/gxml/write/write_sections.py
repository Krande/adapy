from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from ada.config import logger

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
        elif section.type == section.TYPES.IPROFILE and section.w_btn == section.w_top:
            add_i_section(section, sections_elem)
        elif section.type == section.TYPES.IPROFILE and section.w_btn != section.w_top:
            add_unsymm_i_section(section, sections_elem)
        elif section.type == section.TYPES.TPROFILE and section.w_btn != section.w_top:
            add_unsymm_i_section(section, sections_elem)
        elif section.type == section.TYPES.BOX:
            add_box_section(section, sections_elem)
        elif section.type == section.TYPES.CHANNEL:
            add_channel_section(section, sections_elem)
        elif section.type == section.TYPES.FLATBAR:
            add_bar_section(section, sections_elem)
        else:
            logger.error(f"The profile type {section.type} is not yet supported for Genie XML export")


def add_angular_section(section: Section, xml_root: ET.Element):
    section_elem = ET.Element("section", {"name": section.name, "description": ""})
    section_props = ET.Element(
        "l_section",
        dict(
            h=str(section.h),
            b=str(section.w_btn),
            tw=str(section.t_w),
            tf=str(section.t_fbtn),
            fabrication="unknown",
            sfy="1",
            sfz="1",
            general_properties_method="computed",
        ),
    )

    section_elem.append(section_props)
    xml_root.append(section_elem)


def add_pipe_section(section: Section, xml_root: ET.Element):
    section_elem = ET.Element("section", {"name": section.name, "description": ""})

    section_props = ET.Element(
        "pipe_section",
        dict(
            od=str(section.r * 2),
            th=str(section.wt),
            fabrication="unknown",
            sfy="1",
            sfz="1",
            general_properties_method="computed",
        ),
    )

    section_elem.append(section_props)
    xml_root.append(section_elem)


def add_i_section(section: Section, xml_root: ET.Element):
    section_elem = ET.Element("section", {"name": section.name, "description": ""})
    section_props = ET.Element(
        "i_section",
        dict(
            h=str(section.h),
            b=str(section.w_top),
            tw=str(section.t_w),
            tf=str(section.t_fbtn),
            fillet_radius="0.024",
            fabrication="unknown",
            sfy="1",
            sfz="1",
            general_properties_method="computed",
        ),
    )

    section_elem.append(section_props)
    xml_root.append(section_elem)


def add_box_section(section: Section, xml_root: ET.Element):
    # Create the <section> element
    xml_section = ET.Element("section", {"name": section.name})
    box_section = ET.Element(
        "box_section",
        dict(
            h=str(section.h),
            b=str(section.w_btn),
            tw=str(section.t_w),
            tfbot=str(section.t_fbtn),
            tftop=str(section.t_ftop),
            fabrication="unknown",
            sfy="1",
            sfz="1",
            general_properties_method="computed",
        ),
    )

    # Append the <box_section> element to the <section> element
    xml_section.append(box_section)
    xml_root.append(xml_section)
    # You can now append the <section> element to your xml tree


def add_unsymm_i_section(section: Section, xml_root: ET.Element):
    section_elem = ET.Element("section", {"name": section.name})
    section_props = ET.Element(
        "unsymmetrical_i_section",
        dict(
            h=str(section.h),
            tw=str(section.t_w),
            bftop=str(section.w_top),
            bftop1=str(section.w_top / 2),
            bfbot=str(section.w_btn),
            bfbot1=str(section.w_btn / 2),
            tfbot=str(section.t_fbtn),
            tftop=str(section.t_ftop),
            fabrication="unknown",
            sfy="1",
            sfz="1",
            general_properties_method="computed",
        ),
    )

    section_elem.append(section_props)
    xml_root.append(section_elem)


def add_channel_section(section: Section, xml_root: ET.Element):
    section_elem = ET.Element("section", {"name": section.name})
    section_props = ET.Element(
        "channel_section",
        dict(
            h=str(section.h),
            b=str(section.w_btn),
            tw=str(section.t_w),
            tf=str(section.t_fbtn),
            fabrication="unknown",
            sfy="1",
            sfz="1",
            general_properties_method="computed",
        ),
    )

    section_elem.append(section_props)
    xml_root.append(section_elem)


def add_bar_section(section: Section, xml_root: ET.Element):
    # Create the <section> element
    xml_section = ET.Element("section", {"name": section.name})
    bar_section = ET.Element(
        "bar_section",
        dict(
            h=str(section.h),
            b=str(section.w_btn),
            fabrication="unknown",
            sfy="1",
            sfz="1",
            general_properties_method="computed",
        ),
    )

    # Append the <bar_section> element to the <section> element
    xml_section.append(bar_section)
    xml_root.append(xml_section)
    # You can now append the <section> element to your xml tree
