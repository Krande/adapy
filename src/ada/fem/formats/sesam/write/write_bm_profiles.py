from ada import Section
from ada.base.units import Units
from ada.sections import SectionCat

from .write_utils import write_ff


def general_beam(sec: Section, sec_id) -> str:
    p = sec.properties
    comp = 1 if p.modified else 0

    # GENERAL sections often supply only AREA/IX/IY/IZ; the derived GBEAMG fields (product
    # of inertia, section moduli, shear areas/centers, static moments) are then None. The
    # Sesam Input Interface File spec (p.6-50) treats these as optional and SHARY/SHARZ = 0
    # explicitly as "shear not included"; 0.0 is the correct default and doesn't affect the
    # geometry. Coerce None -> 0.0 so the card writes instead of crashing format_data.
    def z(v):
        return 0.0 if v is None else v

    return write_ff(
        "GBEAMG",
        [
            (sec_id, comp, z(p.Ax), z(p.Ix)),
            (z(p.Iy), z(p.Iz), z(p.Iyz), z(p.Wxmin)),
            (z(p.Wymin), z(p.Wzmin), z(p.Shary), z(p.Sharz)),
            (z(p.Shceny), z(p.Shcenz), z(p.Sy), z(p.Sz)),
        ],
    )


def angular(sec: Section, sec_id) -> str:
    """GLSEC: HZ, TY, BY, TZ, SFY, SFZ and K = 0, the web on the negative local y side and the flange
    towards +y (89-7012 7.3.19) -- adapy's angle outline, and the side of its SHCENY. GeniE writes
    K = 0 with the same SHCENY. K = 1, written before, is the mirror; GeniE V8.13-02 and Sestra V11.3
    ignore it (measured: GeniE imports it as K = 0, Sestra's displacements are bit-identical)."""
    p = sec.properties
    width = sec.w_top if sec.w_top is not None else sec.w_btn
    thickness = sec.t_ftop if sec.t_ftop is not None else sec.t_fbtn
    return write_ff(
        "GLSEC",
        [
            (sec_id, sec.h, sec.t_w, width),
            (thickness, p.Sfy, p.Sfz, 0),
        ],
    )


def channel(sec: Section, sec_id) -> str:
    """GCHAN: HZ, TY, BY, TZ -- one width and one thickness for both flanges -- then SFY,
    SFZ, a field not used, and K = 0 (web on the negative local y side; manual 7.3.4)."""
    p = sec.properties
    width = sec.w_top if sec.w_top is not None else sec.w_btn
    thickness = sec.t_ftop if sec.t_ftop is not None else sec.t_fbtn
    return write_ff(
        "GCHAN",
        [
            (sec_id, sec.h, sec.t_w, width),
            (thickness, p.Sfy, p.Sfz, 0),
            (0,),
        ],
    )


def box(sec: Section, sec_id) -> str:
    p = sec.properties
    return write_ff(
        "GBOX",
        [
            (sec_id, sec.h, sec.t_w, sec.t_fbtn),
            (sec.t_ftop, sec.w_btn, p.Sfy, p.Sfz),
        ],
    )


def iprofile(sec: Section, sec_id) -> str:
    p = sec.properties
    return write_ff(
        "GIORH",
        [
            (sec_id, sec.h, sec.t_w, sec.w_top),
            (sec.t_ftop, sec.w_btn, sec.t_fbtn, p.Sfy),
            (p.Sfz,),
        ],
    )


#: GeniE's T (Libraries/tbar.xml, all 202 entries): an unsymmetrical I whose absent flange is 0.001 mm
#: thick and 0.001 mm wider than the web, in metres.
GENIE_T_FLANGE = 1e-6


def tprofile(sec: Section, sec_id) -> str:
    """A T as GeniE writes one: GIORH with the bottom flange 0.001 mm thick and 0.001 mm wider than the web.

    GeniE recomputes GBEAMG from the profile card on import (measured, V8.13-02: a GBEAMG with IX x 10
    is replaced, under COMP 0 or 1). From this card it computes the T (adapy's GBEAMG, SHARY + 6.25e-5
    from the 0.001 mm flange); from the web-wide stub written before (BB = TY, TB = TT) it computed an
    I: SHARY 2.000 x the T's, IX + 1.95 %. ``genie_v8_13_import_T1.FEM``, sections T_GENIE and T_STUB."""
    p = sec.properties
    d = GENIE_T_FLANGE * Units.get_scale_factor(Units.M, sec.units)
    return write_ff(
        "GIORH",
        [
            (sec_id, sec.h, sec.t_w, sec.w_top),
            (sec.t_ftop, sec.t_w + d, d, p.Sfy),
            (p.Sfz,),
        ],
    )


def tubular(sec: Section, sec_id) -> str:
    p = sec.properties
    return write_ff(
        "GPIPE",
        [(sec_id, (sec.r - sec.wt) * 2, sec.r * 2, sec.wt), (p.Sfy, p.Sfz)],
    )


def circular(sec: Section, sec_id) -> str:
    """A solid round bar as GeniE writes one: GPIPE with inner diameter 0 and the wall the radius.

    GeniE V8.13-02 writes ``PipeSection(D, D/2)`` so (S11_ROD in
    ``files/fem_files/sesam/section_props/genie_v8_13_shear_areas_T1.FEM``), and Sestra V11.3 runs
    such a deck. The GBEAMG beside it carries the disc's properties; a GPIPE with a 1 % bore, which
    this used to write, described a tube whose shear area is 1.0 % smaller (5.83159e-3 for
    5.89049e-3 at D100), which GeniE, recomputing from the GPIPE on import, would have used.
    """
    p = sec.properties
    return write_ff(
        "GPIPE",
        [(sec_id, 0.0, sec.r * 2, sec.r), (p.Sfy, p.Sfz)],
    )


def flatbar(sec: Section, sec_id) -> str:
    p = sec.properties
    return write_ff("GBARM", [(sec_id, sec.h, sec.w_top, sec.w_btn), (p.Sfy, p.Sfz)])


def write_bm_section(sec: Section, sec_id: int) -> str:
    bt = SectionCat.BASETYPES

    sec_map = {
        bt.ANGULAR: angular,
        bt.BOX: box,
        bt.CHANNEL: channel,
        bt.IPROFILE: iprofile,
        bt.TPROFILE: tprofile,
        bt.TUBULAR: tubular,
        bt.CIRCULAR: circular,
        bt.FLATBAR: flatbar,
    }

    sec_str = general_beam(sec, sec_id)

    sec_str_writer = sec_map.get(sec.type, None)
    if sec.type == bt.GENERAL:
        return sec_str

    if sec_str_writer is None:
        # The GBEAMG record written above already carries the section's stiffness, so a
        # type with no profile card of its own — POLY — still yields a deck an analysis program
        # can run; it just loses the outline. Saying so and carrying on is exactly what
        # the message promises. It used to say it and then call ``None`` anyway, turning
        # a section adapy understands perfectly well into a TypeError mid-deck.
        from .not_held import STAGE, report

        report().approximated(
            STAGE,
            "Section",
            sec.name,
            "no Sesam profile card for this section type; written as GBEAMG only",
            type=sec.type,
        )
        return sec_str

    sec_str += sec_str_writer(sec, sec_id)

    return sec_str
