import logging

from ada import Section
from ada.sections import SectionCat

from .writer import write_ff


def write_bm_section(sec: Section, sec_id: int) -> str:
    sec.properties.calculate()
    p = sec.properties
    sec_str = write_ff(
        "GBEAMG",
        [
            (sec_id, 0, p.Ax, p.Ix),
            (p.Iy, p.Iz, p.Iyz, p.Wxmin),
            (p.Wymin, p.Wzmin, p.Shary, p.Sharz),
            (p.Scheny, p.Schenz, p.Sy, p.Sz),
        ],
    )

    if SectionCat.is_i_profile(sec.type):
        sec_str += write_ff(
            "GIORH",
            [
                (sec_id, sec.h, sec.t_w, sec.w_top),
                (sec.t_ftop, sec.w_btn, sec.t_fbtn, p.Sfy),
                (p.Sfz,),
            ],
        )
    elif SectionCat.is_hp_profile(sec.type):
        sec_str += write_ff(
            "GLSEC",
            [
                (sec_id, sec.h, sec.t_w, sec.w_btn),
                (sec.t_fbtn, p.Sfy, p.Sfz, 1),
            ],
        )
    elif SectionCat.is_box_profile(sec.type):
        sec_str += write_ff(
            "GBOX",
            [
                (sec_id, sec.h, sec.t_w, sec.t_fbtn),
                (sec.t_ftop, sec.w_btn, p.Sfy, p.Sfz),
            ],
        )
    elif SectionCat.is_tubular_profile(sec.type):
        sec_str += write_ff(
            "GPIPE",
            [(sec_id, (sec.r - sec.wt) * 2, sec.r * 2, sec.wt), (p.Sfy, p.Sfz)],
        )
    elif SectionCat.is_circular_profile(sec.type):
        sec_str += write_ff(
            "GPIPE",
            [(sec_id, (sec.r - sec.r * 0.99) * 2, sec.r * 2, sec.wt), (p.Sfy, p.Sfz)],
        )
    elif SectionCat.is_flatbar(sec.type):
        sec_str += write_ff("GBARM", [(sec_id, sec.h, sec.w_top, sec.w_btn), (p.Sfy, p.Sfz)])
    else:
        logging.error(f'Unable to convert "{sec}". This will be exported as general section only')
    return sec_str
