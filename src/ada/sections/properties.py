from __future__ import annotations

from typing import TYPE_CHECKING, Union

import numpy as np

from ada.config import get_logger

from .categories import SectionCat
from .concept import GeneralProperties

if TYPE_CHECKING:
    from ada.sections.concept import Section

logger = get_logger()


# List of documents the various formulas are based upon
#
#   * StructX.com (https://www.structx.com/geometric_properties.html)
#   * DNVGL. (2011). Appendix B Section properties & consistent units Table of Contentsec. I.
#   * W. Beitz, K.H. KÃ¼ttner: "Dubbel, Taschenbuch fÃ¼r den Maschinenbau" 17. Auflage (17th ed.)
#     Springer-Verlag 1990
#   * Arne Selberg: "StÃ¥lkonstruksjoner" Tapir 1972
#   * sec. Timoshenko: "Strength of Materials, Part I, Elementary Theory and Problems" Third Edition 1995 D.
#     Van Nostrand Company Inc.


def calculate_general_properties(section: Section) -> Union[None, GeneralProperties]:
    """Calculations of cross section properties are based on different sources of information."""
    bt = SectionCat.BASETYPES
    section_map = {
        bt.CIRCULAR: calc_circular,
        bt.IPROFILE: calc_isec,
        bt.BOX: calc_box,
        bt.TUBULAR: calc_tubular,
        bt.ANGULAR: calc_angular,
        bt.CHANNEL: calc_channel,
        bt.FLATBAR: calc_flatbar,
        bt.TPROFILE: calc_isec,
    }

    if section.type == bt.GENERAL:
        logger.info("Skipping re-calculating a general section as it makes no sense")
        return None

    calc_func = section_map.get(section.type, None)

    if calc_func is None:
        raise Exception(
            f'Section type "{section.type}" is not yet supported in the cross section parameter calculations'
        )

    return calc_func(section)


def calc_box(sec: Section) -> GeneralProperties:
    """Calculate box cross section properties"""

    sfy = 1.0
    sfz = 1.0

    Ax = sec.w_btn * sec.t_fbtn + sec.w_top * sec.t_ftop + sec.t_w * (sec.h - (sec.t_fbtn + sec.t_ftop)) * 2

    by = sec.w_top
    tt = sec.t_ftop
    tb = sec.t_fbtn
    ty = sec.t_w
    hz = sec.h

    a = tb / 2
    b = (hz + tb - tt) / 2
    c = hz - tt / 2
    d = sec.h - sec.t_fbtn - sec.t_ftop
    e = by * tb
    f = by * tt
    g = ty * d

    area = e + f + 2 * g
    h = (e * a + f * c + 2 * b * g) / area
    ha = sec.h - (sec.t_fbtn + sec.t_ftop) / 2.0
    hb = sec.w_top - sec.t_w

    Ix = 4 * (ha * hb) ** 2 / (hb / tb + hb / ty + 2 * ha / ty)
    Iy = (by * (tb**3 + tt**3) + 2 * ty * d**3) / 12 + e * (h - a) ** 2 + f * (c - h) ** 2 + 2 * g * (b - h) ** 2

    Iz = ((sec.t_fbtn + sec.t_ftop) * sec.w_top**3 + 2 * d * sec.t_w**3) / 12 + (g * hb**2) / 2
    Iyz = 0
    Wxmin = Ix * (hb + ha) / (ha * hb)
    Wymin = Iy / max(sec.h - h, h)
    Wzmin = 2 * Iz / sec.w_top
    Sy = e * (h - a) + ty * (h - tb) ** 2
    Sz = (sec.t_fbtn + sec.t_ftop) * sec.w_top**2 / 8 + g * hb / 2
    Shary = (Iz / Sz) * 2 * sec.t_w * sfy
    Sharz = (Iy / Sy) * 2 * ty * sfz
    Shceny = 0
    Shcenz = c - h - sec.t_fbtn * ha / (sec.t_fbtn + sec.t_ftop)
    Cy = sec.w_top / 2
    Cz = h
    return GeneralProperties(
        Ax=Ax,
        Ix=Ix,
        Iy=Iy,
        Iz=Iz,
        Iyz=Iyz,
        Wxmin=Wxmin,
        Wymin=Wymin,
        Wzmin=Wzmin,
        Shary=Shary,
        Sharz=Sharz,
        Shceny=Shceny,
        Shcenz=Shcenz,
        Sy=Sy,
        Sz=Sz,
        Sfy=sfy,
        Sfz=sfz,
        Cy=Cy,
        Cz=Cz,
        parent=sec,
    )


def calc_isec(sec: Section) -> GeneralProperties:
    """Calculate I/H cross section properties"""

    sfy = 1.0
    sfz = 1.0
    hz = sec.h
    bt = sec.w_top
    tt = sec.t_ftop
    ty = sec.t_w
    bb = sec.w_btn
    tb = sec.t_fbtn

    Ax = bt * tt + ty * (hz - (tb + tt)) + bb * tb
    hw = hz - tt - tb
    a = tb + hw + tt / 2
    b = tb + hw / 2
    c = tb / 2

    z = (bt * tt * a + hw * ty * b + bb * tb * c) / Ax

    tra = (bt * tb**3) / 12 + bt * tt * (hz - tt / 2 - z) ** 2
    trb = (ty * hw**3) / 12 + ty * hw * (tb + hw / 2 - z) ** 2
    trc = (bb * tb**3) / 12 + bb * tb * (tb / 2 - z) ** 2

    if tt == ty and tt == tb:
        Ix = (tt**3) * (hw + bt + bb - 1.2 * tt) / 3
        Wxmin = Ix / tt
    else:
        Ix = 1.3 * (bt * tt**3 + hw * ty**3 + bb * tb**3) / 3
        Wxmin = Ix / max(tt, ty, tb)

    Iy = tra + trb + trc
    Iz = (tb * bb**3 + hw * ty**3 + tt * bt**3) / 12
    Iyz = 0
    Wymin = Iy / max(hz - z, z)
    Wzmin = 2 * Iz / max(bb, bt)

    # Sy should be checked. Confer older method implementation.
    # Sy = sum(x_i * A_i)
    # Sy = (((tt * bt) ** 2) * (hw / 2 + tt / 2)) * 2
    Sy = Iy / (sec.w_top / 2)

    # Sy = (sec.t_w*sec.h/2)(sec.h/2)
    Sz = (tt * bt**2 + tb * bb**2 + hw * ty**2) / 8
    Shary = (Iz / Sz) * (tb + tt) * sfy
    Sharz = (Iy / Sy) * ty * sfz
    Shceny = 0
    Shcenz = ((hz - tt / 2) * tt * bt**3 + (tb**2) * (bb**3) / 2) / (tt * bt**3 + tb * bb**3) - z
    Cy = bb / 2
    Cz = z

    return GeneralProperties(
        Ax=Ax,
        Ix=Ix,
        Iy=Iy,
        Iz=Iz,
        Iyz=Iyz,
        Wxmin=Wxmin,
        Wymin=Wymin,
        Wzmin=Wzmin,
        Shary=Shary,
        Sharz=Sharz,
        Shceny=Shceny,
        Shcenz=Shcenz,
        Sy=Sy,
        Sz=Sz,
        Sfy=1,
        Sfz=1,
        Cy=Cy,
        Cz=Cz,
        parent=sec,
    )


def calc_angular(sec: Section) -> GeneralProperties:
    """Calculate L cross section properties"""

    # rectangle A properties (web)
    a_w = sec.t_w
    a_h = sec.h - sec.t_fbtn
    a_dy = a_w / 2
    a_dz = sec.t_fbtn + a_h / 2
    a_area = a_w * a_h

    # rectangle B properties (flange)
    b_w = sec.w_btn
    b_h = sec.t_fbtn
    b_dy = b_w / 2
    b_dz = b_h / 2
    b_area = b_h * b_w

    # Find centroid
    c_y = (a_area * a_dy + b_area * b_dy) / (a_area + b_area)
    c_z = (a_area * a_dz + b_area * b_dz) / (a_area + b_area)

    # c_z_opp = sec.h - c_z

    a_dcy = a_dy - c_y
    b_dcy = b_dy - c_y

    a_dcz = a_dz - c_z
    b_dcz = b_dz - c_z

    # Iz_a + A_a*dcy_a**2

    Iz_a = (1 / 12) * a_h * a_w**3 + a_area * a_dcy**2
    Iz_b = (1 / 12) * b_h * b_w**3 + b_area * b_dcy**2
    Iz = Iz_a + Iz_b

    Iy_a = (1 / 12) * a_w * a_h**3 + a_area * a_dcz**2
    Iy_b = (1 / 12) * b_w * b_h**3 + b_area * b_dcz**2
    Iy = Iy_a + Iy_b

    posweb = False

    r = 0

    hz = sec.h
    ty = sec.t_w
    tz = sec.t_fbtn
    by = sec.w_btn

    sfy = 1.0
    sfz = 1.0
    hw = hz - tz
    b = tz - hw / 2.0
    c = tz / 2.0
    piqrt = np.arctan(1.0)
    Ax = ty * hw + by * tz + (1 - piqrt) * r**2
    y = (hw * ty**2 + tz * by**2) / (2 * Ax)
    z = (hw * b * ty + tz * by * c) / Ax
    d = 6 * r + 2 * (ty + tz - np.sqrt(4 * r * (2 * r + ty + tz) + 2 * ty * tz))
    e = hw + tz - z
    f = hw - e
    ri = y - ty
    rj = by - y
    rk = ri + 0.5 * ty
    rl = z - c

    if tz >= ty:
        h = hw
    else:
        raise ValueError("Currently not implemented this yet")

    Ix = (1 / 3) * (by * tz**3 + (hz - tz) * ty**3)
    Iyz = (rl * tz / 2) * (y**2 - rj**2) - (rk * ty / 2) * (e**2 - f**2)

    Wxmin = Ix / d
    Wymin = Iy / max(z, hz - h)
    Wzmin = Iz / max(y, rj)
    Sy = (ty * e**2) / 2
    Sz = (tz * rj**2) / 2
    Shary = (Iz * tz / Sz) * sfy
    Sharz = (Iy * tz / Sy) * sfz

    if posweb:
        Iyz = -Iyz
        Shceny = rk
        Cy = by - y
    else:
        Shceny = -rk
        Cy = y
    Shcenz = -rl
    Cz = z

    return GeneralProperties(
        Ax=Ax,
        Ix=Ix,
        Iy=Iy,
        Iz=Iz,
        Iyz=Iyz,
        Wxmin=Wxmin,
        Wymin=Wymin,
        Wzmin=Wzmin,
        Shary=Shary,
        Sharz=Sharz,
        Shceny=Shceny,
        Shcenz=Shcenz,
        Sy=Sy,
        Sz=Sz,
        Sfy=1,
        Sfz=1,
        Cy=Cy,
        Cz=Cz,
        parent=sec,
    )


def calc_tubular(sec: Section) -> GeneralProperties:
    """Calculate Tubular cross section properties"""

    t = sec.wt
    sfy = 1.0
    sfz = 1.0

    dy = sec.r * 2
    di = dy - 2 * t
    Ax = np.pi * sec.r**2 - np.pi * (sec.r - t) ** 2
    Ix = 0.5 * np.pi * ((dy / 2) ** 4 - (di / 2) ** 4)
    Iy = Ix / 2
    Iz = Iy
    Iyz = 0
    Wxmin = 2 * Ix / dy
    Wymin = 2 * Iy / dy
    Wzmin = 2 * Iz / dy
    Sy = (dy**3 - di**3) / 12
    Sz = Sy
    Shary = (2 * Iz * t / Sy) * sfy
    Sharz = (2 * Iy * t / Sz) * sfz
    Shceny = 0
    Shcenz = 0
    Cy = 0.0
    Cz = 0.0

    return GeneralProperties(
        Ax=Ax,
        Ix=Ix,
        Iy=Iy,
        Iz=Iz,
        Iyz=Iyz,
        Wxmin=Wxmin,
        Wymin=Wymin,
        Wzmin=Wzmin,
        Shary=Shary,
        Sharz=Sharz,
        Shceny=Shceny,
        Shcenz=Shcenz,
        Sy=Sy,
        Sz=Sz,
        Sfy=1,
        Sfz=1,
        Cy=Cy,
        Cz=Cz,
        parent=sec,
    )


def calc_circular(sec: Section) -> GeneralProperties:
    Sfy = 1.0
    Sfz = 1.0
    Iyz = 0.0

    Ax = np.pi * sec.r**2
    Iy = (np.pi * sec.r**4) / 4
    Iz = Iy
    Ix = 0.5 * np.pi * sec.r**4
    Wymin = 0.25 * np.pi * sec.r**3
    Wzmin = Wymin

    Wxmin = Ix / sec.r

    t = sec.r * 0.99
    dy = sec.r * 2
    di = dy - 2 * t
    Sy = (dy**3 - di**3) / 12
    Sz = Sy
    Shary = (2 * Iz * t / Sy) * Sfy
    Sharz = (2 * Iy * t / Sz) * Sfz
    Shceny = 0
    Shcenz = 0
    Cy = 0.0
    Cz = 0.0

    return GeneralProperties(
        Ax=Ax,
        Ix=Ix,
        Iy=Iy,
        Iz=Iz,
        Iyz=Iyz,
        Wxmin=Wxmin,
        Wymin=Wymin,
        Wzmin=Wzmin,
        Shary=Shary,
        Sharz=Sharz,
        Shceny=Shceny,
        Shcenz=Shcenz,
        Sy=Sy,
        Sz=Sz,
        Sfy=Sfy,
        Sfz=Sfz,
        Cy=Cy,
        Cz=Cz,
        parent=sec,
    )


def calc_flatbar(sec: Section) -> GeneralProperties:
    """Flatbar (not supporting unsymmetric profile)"""
    w = sec.w_btn
    hz = sec.h

    a = 0.0
    h = hz * w / (2 * w)
    b = w / 2
    d = 0

    Sfy = 1.0
    Sfz = 1.0

    Ax = w * hz
    Iy = w * hz**3 / 12
    Iz = hz * w**3 / 12

    bm = 2 * w * hz**2 / (hz**2 + Ax**2)
    Wymin = Iy / max(h, d)
    Wzmin = 2 * Iz / max(w, w)
    Iyz = 0.0
    if hz == bm:
        ca = 0.141
        cb = 0.208
        Ix = ca * hz**4
        Wxmin = cb * hz**3
    elif hz < bm:
        cn = bm / hz
        ca = (1 - 0.63 / cn + 0.052 / cn**5) * 3
        cb = ca / (1 - 0.63 / (1 + cn**3))
        Ix = ca * bm * hz**3
        Wxmin = cb * bm * hz**2
    else:
        cn = hz / bm
        ca = (1 - 0.63 / cn + 0.052 / cn**5) * 3
        cb = ca / (1 - 0.63 / (1 + cn**3))
        Ix = ca * hz * bm**3
        Wxmin = cb * hz * bm**3

    Sy = (w * h**2) / 2 + (b - w / 2) * (h**2) / 3
    Sz = hz * ((w**2) / 8 + a * (w / 4 + a / 6))

    Shary = Iz * hz * Sfy / Sz
    Sharz = 2 * Iy * b * Sfz / Sy

    Shceny = 0.0
    Shcenz = 0.0
    Cy = w / 2
    Cz = hz

    return GeneralProperties(
        Ax=Ax,
        Ix=Ix,
        Iy=Iy,
        Iz=Iz,
        Iyz=Iyz,
        Wxmin=Wxmin,
        Wymin=Wymin,
        Wzmin=Wzmin,
        Shary=Shary,
        Sharz=Sharz,
        Shceny=Shceny,
        Shcenz=Shcenz,
        Sy=Sy,
        Sz=Sz,
        Sfy=Sfy,
        Sfz=Sfz,
        Cy=Cy,
        Cz=Cz,
        parent=sec,
    )


def calc_channel(sec: Section) -> GeneralProperties:
    """Calculate section properties of a channel profile"""
    posweb = False
    hz = sec.h
    ty = sec.t_w
    tz = sec.t_fbtn
    by = sec.w_btn
    sfy = 1.0
    sfz = 1.0

    a = hz - 2 * tz
    Ax = 2 * by * tz + a * ty
    y = (2 * tz * by**2 + a * ty**2) / (2 * Ax)
    Iy = (ty * a**3) / 12 + 2 * ((by * tz**3) / 12 + by * tz * ((a + tz) / 2) ** 2)

    if tz == ty:
        Ix = ty**3 * (2 * by + a - 2.6 * ty) / 3
        Wxmin = Ix / Iy
    else:
        Ix = 1.12 * (2 * by * tz**3 + a * ty**3) / 3
        Wxmin = Ix / max(tz, ty)

    Iz = 2 * ((tz * by**3) / 12 + tz * by * (by / 2 - y) ** 2) + (a * ty**3) / 12 + a * ty * (y - ty / 2) ** 2
    Iyz = 0
    Wymin = 2 * Iy / hz
    Wzmin = Iz / max(by - y, y)
    Sy = by * tz * (tz + a) / 2 + (ty * a**2) / 8
    Sz = tz * (by - y) ** 2

    Shary = (Iz / Sz) * (2 * tz) * sfy
    Sharz = (Iy / Sy) * ty * sfz

    if tz == ty:
        q = ((by - ty / 2) ** 2) * ((hz - tz) ** 2) * tz / 4 * Iy
    else:
        q = ((by - ty / 2) ** 2) * tz / (2 * (by - ty / 2) * tz + (hz - tz) * ty / 3)

    if posweb:
        Shceny = y - ty / 2 + q
        Cy = by
    else:
        Shceny = -(y - ty / 2 + q)
        Cy = by - y

    Cz = hz / 2
    Shcenz = 0

    return GeneralProperties(
        Ax=Ax,
        Ix=Ix,
        Iy=Iy,
        Iz=Iz,
        Iyz=Iyz,
        Wxmin=Wxmin,
        Wymin=Wymin,
        Wzmin=Wzmin,
        Shary=Shary,
        Sharz=Sharz,
        Shceny=Shceny,
        Shcenz=Shcenz,
        Sy=Sy,
        Sz=Sz,
        Sfy=sfy,
        Sfz=sfz,
        Cy=Cy,
        Cz=Cz,
        parent=sec,
    )
