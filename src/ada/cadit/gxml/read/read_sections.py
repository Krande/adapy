from ada import Part, Section
from ada.api.containers import Sections
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
        unsymmetrical_i_section=unsymm_isec,  # Note identifies Genie unsymmetrical_i_section and create TPROFILE in ada, since Tprofile does not exist in Genie!
        pipe_section=pipe_section,
        channel_section=channel_section,
        bar_section=bar_section,
        general_section=general_section,
        cone_section=cone_section,
        pgb_section=pgb_section,
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
        sec_type=Section.TYPES.BOX,
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


_DEGENERATE_FLANGE_EPS = 1e-3  # 1 mm (sections stored in metres)


def _flange_absent(t_flange: float, w_flange: float, t_w: float) -> bool:
    """A flange counts as absent when it has no real overhang past the
    web or near-paper thickness. Captures the two Genie idioms for
    hiding a T-section inside an ``unsymmetrical_i_section``:

    * Adapy's TPROFILE round-trip: ``w_flange == t_w`` (flange flush
      with the web, zero overhang).
    * Manual T in Genie: ``t_flange`` ~ 0 with ``w_flange`` ~ ``t_w``
      (audit-#5256 dataset, ``t_ftop=0.0001, w_top=t_w``).
    """
    no_overhang = (w_flange - t_w) <= _DEGENERATE_FLANGE_EPS
    paper_thin = t_flange <= _DEGENERATE_FLANGE_EPS
    return no_overhang or paper_thin


def unsymm_isec(name, sec_prop) -> Section:

    h = float(sec_prop.attrib["h"])
    w_btn = float(sec_prop.attrib["bfbot"])
    w_top = float(sec_prop.attrib["bftop"])
    t_w = float(sec_prop.attrib["tw"])
    t_ftop = float(sec_prop.attrib["tftop"])
    t_fbtn = float(sec_prop.attrib["tfbot"])

    # Genie has no native T-section, so T-shapes are encoded as
    # ``unsymmetrical_i_section`` with one flange collapsed onto the
    # web. Two encodings show up in real data:
    #
    # 1. Top flange real, bottom collapsed — adapy's TPROFILE export
    #    pattern. Maps straight to adapy TPROFILE.
    # 2. Bottom flange real, top collapsed — flange-down T authored
    #    natively in Genie (audit-#5256 has 280 of these). Adapy has
    #    no flange-down orientation today, so we re-encode with the
    #    real flange swapped to the top. The rendered shape ends up
    #    visually flipped vs. the Genie source, but every beam now
    #    tessellates instead of getting silently dropped from the GLB
    #    because ``iprofiles`` produced duplicate vertices.
    #
    # Genuine asymmetric I (both flanges with real overhang) falls
    # through to IPROFILE unchanged.
    top_absent = _flange_absent(t_ftop, w_top, t_w)
    btn_absent = _flange_absent(t_fbtn, w_btn, t_w)

    if btn_absent and not top_absent:
        # Adapy convention — flange up.
        return Section(
            name=name,
            sec_type=Section.TYPES.TPROFILE,
            sec_str=name,
            h=h,
            w_btn=w_btn,
            w_top=w_top,
            t_w=t_w,
            t_ftop=t_ftop,
            t_fbtn=t_fbtn,
        )
    if top_absent and not btn_absent:
        # Inverted T — re-encode into adapy convention (flange-up)
        # AND mark the section so the beam reader flips the local-z
        # vector. The geometry then renders flange-down, matching
        # the Genie source. Without the flip every beam carrying an
        # inverted T would point its flange the wrong way — the
        # user would see them upside-down.
        return Section(
            name=name,
            sec_type=Section.TYPES.TPROFILE,
            sec_str=name,
            h=h,
            w_btn=w_top,
            w_top=w_btn,
            t_w=t_w,
            t_ftop=t_fbtn,
            t_fbtn=t_ftop,
            metadata={"gxml_flange_down": True},
        )
    return Section(
        name=name,
        sec_type=Section.TYPES.IPROFILE,
        sec_str=name,
        h=h,
        w_btn=w_btn,
        w_top=w_top,
        t_w=t_w,
        t_ftop=t_ftop,
        t_fbtn=t_fbtn,
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
    return Section(name, sec_type=Section.TYPES.GENERAL, genprops=GeneralProperties(Ax=0.1))


def pgb_section(name, sec_prop):
    # circ = [(-1, -1), (1, -1), (1, 1), (-1, 1)]
    h, b, tw, otw, tf = [float(sec_prop.attrib[x]) for x in ("h", "b", "tw", "otw", "tf")]

    # ot = [(x * h / 2, y * b / 2) for x, y in circ]
    # it1 = [(x * h / 2 + otw, y * b / 2 + tf) for x, y in circ]
    # it2 = [(b / 2 + tw + x * h / 2 + otw, y * b / 2 + tf) for x, y in circ]

    return Section(name, Section.TYPES.BOX, h=h, w_btn=b, w_top=b, t_w=otw, t_fbtn=tf, t_ftop=tf)
