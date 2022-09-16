from ada import Part, Section
from ada.concepts.containers import Sections
from ada.sections import GeneralProperties


def get_sections(xml_root, parent: Part) -> Sections:
    all_secs = xml_root.findall(".//section")
    sections = [interpret_section_props(sec_el.attrib["name"], sec_el[0], parent) for sec_el in all_secs]
    return Sections(sections, parent=parent)


def interpret_section_props(name, sec_prop, parent: Part) -> Section:
    sec_map = dict(
        box_section=box_sec,
        i_section=isec,
        l_section=angular,
        unsymmetrical_i_section=unsymm_isec,
        pipe_section=pipe_section,
        channel_section=channel_section,
        bar_section=bar_section,
        general_section=general_section,
        cone_section=cone_section,
    )
    sec_interpreter = sec_map.get(sec_prop.tag, None)

    if sec_interpreter is None:
        raise ValueError(f"Missing property {sec_prop.tag}")

    section = sec_interpreter(name, sec_prop)
    section.parent = parent

    return section


def box_sec(name, sec_prop) -> Section:
    return Section(
        name=name,
        sec_type="BG",
        sec_str=name,
        h=float(sec_prop.attrib["h"]),
        w_top=float(sec_prop.attrib["b"]),
        w_btn=float(sec_prop.attrib["b"]),
        t_w=float(sec_prop.attrib["tw"]),
        t_ftop=float(sec_prop.attrib["tftop"]),
        t_fbtn=float(sec_prop.attrib["tfbot"]),
    )


def angular(name, sec_prop) -> Section:
    return Section(
        name=name,
        sec_type=Section.TYPES.ANGULAR,
        sec_str=name,
        h=float(sec_prop.attrib["h"]),
        w_btn=float(sec_prop.attrib["b"]),
        t_w=float(sec_prop.attrib["tw"]),
        t_fbtn=float(sec_prop.attrib["tf"]),
    )


def isec(name, sec_prop) -> Section:
    return Section(
        name=name,
        sec_type=Section.TYPES.IPROFILE,
        sec_str=name,
        h=float(sec_prop.attrib["h"]),
        w_top=float(sec_prop.attrib["b"]),
        w_btn=float(sec_prop.attrib["b"]),
        t_w=float(sec_prop.attrib["tw"]),
        t_ftop=float(sec_prop.attrib["tf"]),
        t_fbtn=float(sec_prop.attrib["tf"]),
    )


def unsymm_isec(name, sec_prop) -> Section:
    return Section(
        name=name,
        sec_type=Section.TYPES.IPROFILE,
        sec_str=name,
        h=float(sec_prop.attrib["h"]),
        w_btn=float(sec_prop.attrib["bfbot"]),
        w_top=float(sec_prop.attrib["bftop"]),
        t_w=float(sec_prop.attrib["tw"]),
        t_ftop=float(sec_prop.attrib["tftop"]),
        t_fbtn=float(sec_prop.attrib["tfbot"]),
    )


def pipe_section(name, sec_prop) -> Section:
    return Section(
        name=name,
        sec_type=Section.TYPES.TUBULAR,
        sec_str=name,
        r=float(sec_prop.attrib["od"]) / 2,
        wt=float(sec_prop.attrib["th"]),
    )


def channel_section(name, sec_prop) -> Section:
    return Section(
        name=name,
        sec_type=Section.TYPES.CHANNEL,
        sec_str=name,
        h=float(sec_prop.attrib["h"]),
        w_top=float(sec_prop.attrib["b"]),
        w_btn=float(sec_prop.attrib["b"]),
        t_w=float(sec_prop.attrib["tw"]),
        t_ftop=float(sec_prop.attrib["tf"]),
        t_fbtn=float(sec_prop.attrib["tf"]),
    )


def bar_section(name, sec_prop) -> Section:
    return Section(
        name=name,
        sec_type=Section.TYPES.FLATBAR,
        sec_str=name,
        h=float(sec_prop.attrib["h"]),
        w_top=float(sec_prop.attrib["b"]),
        w_btn=float(sec_prop.attrib["b"]),
    )


def general_section(name, sec_prop) -> Section:
    return Section(
        name=name,
        sec_str=name,
        sec_type=Section.TYPES.GENERAL,
        genprops=GeneralProperties(
            Ax=float(sec_prop.attrib["area"]),
            Ix=float(sec_prop.attrib["ix"]),
            Iy=float(sec_prop.attrib["iy"]),
            Iz=float(sec_prop.attrib["iz"]),
            Iyz=float(sec_prop.attrib["iyz"]),
            Wxmin=float(sec_prop.attrib["wxmin"]),
            Wymin=float(sec_prop.attrib["wymin"]),
            Wzmin=float(sec_prop.attrib["wzmin"]),
            Shary=float(sec_prop.attrib["shary"]),
            Sharz=float(sec_prop.attrib["sharz"]),
            Shceny=float(sec_prop.attrib["shceny"]),
            Shcenz=float(sec_prop.attrib["shcenz"]),
            Sy=float(sec_prop.attrib["sy"]),
            Sz=float(sec_prop.attrib["sz"]),
            Sfy=float(sec_prop.attrib["sfy"]),
            Sfz=float(sec_prop.attrib["sfz"]),
        ),
    )


def cone_section(name, sec_prop) -> Section:
    return Section(name, sec_type=None)
