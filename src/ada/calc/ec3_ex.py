# An example of Eurocode equations
from __future__ import annotations

import math
from typing import TYPE_CHECKING

from ada.sections.categories import BaseTypes

if TYPE_CHECKING:
    from ada.fem import Elem


def ec3_654(elem: Elem, forces: list[float], buckling_length) -> float:
    if len(forces) != 6:
        raise ValueError("Length of Forces must be 6")

    m_ed = forces[4]
    p = elem.fem_sec.section.properties

    wy = p.Wymin
    fy = elem.fem_sec.material.model.sig_y
    x_lt = lat_buckling_xlt(elem, buckling_length)

    gam_m1 = 1.15
    mb_rd = x_lt * wy * fy / gam_m1
    return m_ed / mb_rd


def lat_buckling_xlt(elem, buckling_length, is_welded=False):
    s = elem.fem_sec.section
    p = s.properties
    wy = p.Wymin
    fy = elem.fem_sec.material.model.sig_y

    imp_fac = dict(a=0.21, b=0.34, c=0.49, d=0.76)
    if elem.fem_sec.section.type == BaseTypes.IPROFILE:
        if is_welded:
            if s.h / s.w_btn <= 2:
                a_lt = imp_fac["a"]
            else:
                a_lt = imp_fac["b"]
        else:
            if s.h / s.w_btn <= 2:
                a_lt = imp_fac["c"]
            else:
                a_lt = imp_fac["d"]
    else:
        a_lt = imp_fac["d"]

    m_cr = critical_moment(elem, buckling_length)
    lam_lt = math.sqrt(wy * fy / m_cr)
    phi_lt = 0.5 * (1 + a_lt * (lam_lt - 0.2) + lam_lt**2)
    xi_lt = 1.0 / (phi_lt + math.sqrt(phi_lt**2 - lam_lt**2))

    return min(xi_lt, 1.0)


def critical_moment(elem, length):
    # Todo: this is not at all correct. Will finish this later
    p = elem.fem_sec.section.properties

    g = elem.fem_sec.material.model.G
    e = elem.fem_sec.material.model.E
    if elem.fem_sec.section.type == BaseTypes.IPROFILE:
        m0_cr = (math.pi / length) * math.sqrt(g * p.Ix * p.Iz)
        i_w = 0
    else:
        raise NotImplementedError()

    my_cr = math.sqrt(1 + ((math.pi / length) ** 2) * (e * i_w / (g * p.Ix)))

    return m0_cr * my_cr
