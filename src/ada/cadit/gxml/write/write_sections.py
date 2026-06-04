from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

from ada.config import logger

if TYPE_CHECKING:
    from ada import Part, Section


def get_section_props(section: Section) -> ET.Element | None:
    if section.type == section.TYPES.ANGULAR:
        return to_gxml_angular_section(section)
    elif section.type == section.TYPES.TUBULAR:
        return to_gxml_pipe_section(section)
    elif section.type == section.TYPES.IPROFILE and section.w_btn == section.w_top:
        return to_gxml_i_section(section)
    elif section.type == section.TYPES.IPROFILE and section.w_btn != section.w_top:
        return to_gxml_unsymm_i_section(section)
    elif section.type == section.TYPES.TPROFILE and section.w_btn != section.w_top:
        return to_gxml_unsymm_i_section(section)
    elif section.type == section.TYPES.BOX:
        return to_gxml_box_section(section)
    elif section.type == section.TYPES.CHANNEL:
        return to_gxml_channel_section(section)
    elif section.type == section.TYPES.FLATBAR:
        return to_gxml_bar_section(section)
    elif section.type == section.TYPES.GENERAL:
        return to_gxml_general_section(section)
    else:
        logger.error(f"The profile type {section.type} is not yet supported for Genie XML export")
        return None


def add_sections(root: ET.Element, part: Part):
    from ada import BeamTapered

    sections_elem = ET.Element("sections")

    # Add the new element underneath <properties>
    root.append(sections_elem)

    for section in part.sections:
        section_props = get_section_props(section)
        if section_props is None:
            # Unsupported section type (already logged by get_section_props).
            # Skip it rather than appending None — a single unsupported
            # section must not abort the whole export with a TypeError.
            continue
        section_elem = ET.SubElement(sections_elem, "section", {"name": section.name, "description": ""})
        section_elem.append(section_props)

    tapered_sections: list[tuple[Section, Section]] = []
    for bm in part.get_all_physical_objects(by_type=BeamTapered):
        profile1 = bm.section
        profile2 = bm.taper
        profile_tup = (profile1, profile2)
        if profile_tup not in tapered_sections:
            tapered_sections.append(profile_tup)

    for profile1, profile2 in tapered_sections:
        sec1_props = get_section_props(profile1)
        sec2_props = get_section_props(profile2)
        if sec1_props is None or sec2_props is None:
            # Either end uses an unsupported section type (already logged).
            # Skip the tapered pair rather than aborting the export.
            continue
        section_elem = ET.SubElement(
            sections_elem, "section", {"name": f"{profile1.name}_{profile2.name}", "description": ""}
        )
        tapered_section = ET.SubElement(
            section_elem,
            "tapered_section",
            dict(fabrication="unknown", sfy="1", sfz="1", general_properties_method="computed"),
        )
        section1 = ET.SubElement(tapered_section, "section1")
        section1.append(sec1_props)
        section2 = ET.SubElement(tapered_section, "section2")
        section2.append(sec2_props)


def to_gxml_angular_section(section: Section):

    return ET.Element(
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


def to_gxml_pipe_section(section: Section):
    return ET.Element(
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


def to_gxml_i_section(section: Section):
    return ET.Element(
        "i_section",
        dict(
            h=str(section.h),
            b=str(section.w_top),
            tw=str(section.t_w),
            tf=str(section.t_fbtn),
            fillet_radius="0.00",  # note this will be used in genie cog calc!
            fabrication="unknown",
            sfy="1",
            sfz="1",
            general_properties_method="computed",
        ),
    )


def to_gxml_box_section(section: Section):
    return ET.Element(
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


def to_gxml_unsymm_i_section(section: Section):
    # Note: this logic must be aligned with get_alignment in read_beams.js
    return ET.Element(
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


def to_gxml_channel_section(section: Section) -> ET.Element:
    return ET.Element(
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


def to_gxml_bar_section(section: Section) -> ET.Element:
    return ET.Element(
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


def to_gxml_general_section(section: Section) -> ET.Element | None:
    # Numeric (GENERAL) section — no named profile shape, just explicit
    # cross-section properties (Sesam GBEAMG, IFC general profiles, …).
    # Emit a Genie ``<general_section>`` carrying exactly the attributes
    # the gxml reader (read_sections.general_section) parses back, so the
    # section round-trips. ``general_properties_method="explicit"`` tells
    # Genie to use the given numbers rather than recomputing from geometry
    # (there is none).
    gp = section.properties
    if gp is None:
        logger.error(f"GENERAL section {section.name!r} has no properties; skipping Genie XML export")
        return None

    def _v(value, default=0.0) -> str:
        return str(default if value is None else value)

    return ET.Element(
        "general_section",
        dict(
            area=_v(gp.Ax),
            ix=_v(gp.Ix),
            iy=_v(gp.Iy),
            iz=_v(gp.Iz),
            iyz=_v(gp.Iyz),
            wxmin=_v(gp.Wxmin),
            wymin=_v(gp.Wymin),
            wzmin=_v(gp.Wzmin),
            shary=_v(gp.Shary),
            sharz=_v(gp.Sharz),
            shceny=_v(gp.Shceny),
            shcenz=_v(gp.Shcenz),
            sy=_v(gp.Sy),
            sz=_v(gp.Sz),
            sfy=_v(gp.Sfy, 1),
            sfz=_v(gp.Sfz, 1),
            fabrication="unknown",
            general_properties_method="explicit",
        ),
    )
