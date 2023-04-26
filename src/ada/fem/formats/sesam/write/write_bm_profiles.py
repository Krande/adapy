from ada import Section
from ada.config import logger
from ada.sections import SectionCat

from .write_utils import write_ff


def general_beam(sec: Section, sec_id) -> str:
    p = sec.properties
    comp = 1 if p.modified else 0
    return write_ff(
        "GBEAMG",
        [
            (sec_id, comp, p.Ax, p.Ix),
            (p.Iy, p.Iz, p.Iyz, p.Wxmin),
            (p.Wymin, p.Wzmin, p.Shary, p.Sharz),
            (p.Shceny, p.Shcenz, p.Sy, p.Sz),
        ],
    )


def angular(sec: Section, sec_id) -> str:
    p = sec.properties
    return write_ff(
        "GLSEC",
        [
            (sec_id, sec.h, sec.t_w, sec.w_btn),
            (sec.t_fbtn, p.Sfy, p.Sfz, 1),
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


def tubular(sec: Section, sec_id) -> str:
    p = sec.properties
    return write_ff(
        "GPIPE",
        [(sec_id, (sec.r - sec.wt) * 2, sec.r * 2, sec.wt), (p.Sfy, p.Sfz)],
    )


def circular(sec: Section, sec_id) -> str:
    p = sec.properties
    return write_ff(
        "GPIPE",
        [(sec_id, (sec.r - sec.r * 0.99) * 2, sec.r * 2, sec.wt), (p.Sfy, p.Sfz)],
    )


def flatbar(sec: Section, sec_id) -> str:
    p = sec.properties
    return write_ff("GBARM", [(sec_id, sec.h, sec.w_top, sec.w_btn), (p.Sfy, p.Sfz)])


def write_bm_section(sec: Section, sec_id: int) -> str:
    bt = SectionCat.BASETYPES

    sec_map = {
        bt.ANGULAR: angular,
        bt.BOX: box,
        bt.IPROFILE: iprofile,
        bt.TPROFILE: iprofile,
        bt.TUBULAR: tubular,
        bt.CIRCULAR: circular,
        bt.FLATBAR: flatbar,
    }

    sec_str = general_beam(sec, sec_id)

    sec_str_writer = sec_map.get(sec.type, None)
    if sec.type == bt.GENERAL:
        return sec_str

    if sec_str_writer is None:
        logger.error(f'Unable to convert "{sec}". This will be exported as general section only')

    sec_str += sec_str_writer(sec, sec_id)

    return sec_str
