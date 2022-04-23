from typing import List, Tuple

from ada.concepts.curves import CurvePoly
from ada.core.utils import roundoff as rd

from .concept import Section, SectionParts, SectionProfile

build_props = dict(origin=(0, 0, 0), xdir=(1, 0, 0), normal=(0, 0, 1))


def build_disconnected(input_curve: List[Tuple[tuple, tuple]]) -> List[CurvePoly]:
    return [CurvePoly(x, is_closed=False, **build_props) for x in input_curve]


def build_joined(input_curve: List[tuple]) -> CurvePoly:
    return CurvePoly(input_curve, **build_props)


def angular(sec: Section, return_solid) -> SectionProfile:
    h = sec.h
    wbtn = sec.w_btn
    p1 = (0.0, 0.0)
    p2 = (0.0, -h)
    p3 = (wbtn, -h)

    outer_curve_disconnected = None
    outer_curve = None
    shell_thick_map = None

    if return_solid is False:
        disconnected = True
        outer_curve_disconnected = build_disconnected([(p1, p2), (p2, p3)])
        shell_thick_map = [(SectionParts.WEB, sec.t_w), (SectionParts.BTN_FLANGE, sec.t_fbtn)]
    else:
        disconnected = False
        tf = sec.t_fbtn
        tw = sec.t_w
        p4 = (wbtn, -h + tf)
        p5 = (tw, -h + tf)
        p6 = (tw, 0.0)
        outer_curve = build_joined([p1, p2, p3, p4, p5, p6])

    return SectionProfile(
        sec,
        return_solid,
        outer_curve=outer_curve,
        outer_curve_disconnected=outer_curve_disconnected,
        disconnected=disconnected,
        shell_thickness_map=shell_thick_map,
    )


def iprofiles(sec: Section, return_solid) -> SectionProfile:
    h = sec.h
    wbtn = sec.w_btn
    wtop = sec.w_top

    # top flange
    c1 = (-wtop / 2, h / 2)
    c2 = (wtop / 2, h / 2)
    # web
    p3 = (0.0, h / 2)
    p4 = (0.0, -h / 2)
    # bottom flange
    c3 = (-wbtn / 2, -h / 2)
    c4 = (wbtn / 2, -h / 2)

    outer_curve = None
    outer_curve_disconnected = None
    shell_thick_map = None
    if return_solid is False:
        disconnected = True
        input_curve = [(c1, c2), (p3, p4), (c3, c4)]
        outer_curve_disconnected = build_disconnected(input_curve)
        shell_thick_map = [
            (SectionParts.TOP_FLANGE, sec.t_ftop),
            (SectionParts.WEB, sec.t_w),
            (SectionParts.BTN_FLANGE, sec.t_fbtn),
        ]
    else:
        disconnected = False
        tfbtn = sec.t_fbtn
        tftop = sec.t_ftop
        tw = sec.t_w
        p3 = (wtop / 2, h / 2 - tftop)
        p4 = (tw / 2, h / 2 - tftop)
        p5 = (tw / 2, -h / 2 + tfbtn)
        p6 = (wbtn / 2, -h / 2 + tfbtn)
        p7 = (-wbtn / 2, -h / 2 + tfbtn)
        p8 = (-tw / 2, -h / 2 + tfbtn)
        p9 = (-tw / 2, h / 2 - tftop)
        p10 = (-wtop / 2, h / 2 - tftop)
        input_curve = [c1, c2, p3, p4, p5, p6, c4, c3, p7, p8, p9, p10]
        outer_curve = build_joined(input_curve)

    return SectionProfile(
        sec,
        return_solid,
        outer_curve=outer_curve,
        outer_curve_disconnected=outer_curve_disconnected,
        disconnected=disconnected,
        shell_thickness_map=shell_thick_map,
    )


def tprofiles(sec: Section, return_solid) -> SectionProfile:
    h = sec.h
    wtop = sec.w_top

    # top flange
    c1 = (-wtop / 2, h / 2)
    c2 = (wtop / 2, h / 2)
    # web
    p3 = (0.0, h / 2)
    p4 = (0.0, -h / 2)

    outer_curve = None
    outer_curve_disconnected = None
    shell_thick_map = None
    if return_solid is False:
        disconnected = True
        input_curve = [(c1, c2), (p3, p4)]
        outer_curve_disconnected = build_disconnected(input_curve)
        shell_thick_map = [(SectionParts.TOP_FLANGE, sec.t_ftop), (SectionParts.WEB, sec.t_w)]
    else:
        disconnected = False
        tftop = sec.t_ftop
        tw = sec.t_w
        p3 = (wtop / 2, h / 2 - tftop)
        p4 = (tw / 2, h / 2 - tftop)
        p5 = (tw / 2, -h / 2)
        p8 = (-tw / 2, -h / 2)
        p9 = (-tw / 2, h / 2 - tftop)
        p10 = (-wtop / 2, h / 2 - tftop)
        input_curve = [c1, c2, p3, p4, p5, p8, p9, p10]
        outer_curve = build_joined(input_curve)

    return SectionProfile(
        sec,
        return_solid,
        outer_curve=outer_curve,
        outer_curve_disconnected=outer_curve_disconnected,
        disconnected=disconnected,
        shell_thickness_map=shell_thick_map,
    )


def box(sec: Section, return_solid) -> SectionProfile:
    h = sec.h
    wtop = sec.w_top
    wbtn = sec.w_btn

    p1 = (rd(-wtop / 2), rd(h / 2))
    p2 = (rd(wtop / 2), rd(h / 2))
    p3 = (rd(wbtn / 2), rd(-h / 2))
    p4 = (rd(-wbtn / 2), rd(-h / 2))

    inner_curve = None
    if return_solid is False:
        outer_curve = build_joined([p1, p2, p3, p4])
    else:
        tftop = sec.t_fbtn
        tfbtn = sec.t_fbtn
        tw = sec.t_w
        p5 = (rd(-wtop / 2 + tw), rd(h / 2 - tftop))
        p6 = (rd(wtop / 2 - tw), rd(h / 2 - tftop))
        p7 = (rd(wbtn / 2 - tw), rd(-h / 2 + tfbtn))
        p8 = (rd(-wbtn / 2 + tw), rd(-h / 2 + tfbtn))

        outer_curve = build_joined([p1, p2, p3, p4])
        inner_curve = build_joined([p5, p6, p7, p8])

    return SectionProfile(
        sec,
        return_solid,
        outer_curve=outer_curve,
        inner_curve=inner_curve,
        disconnected=False,
    )


def flatbar(sec: Section, return_solid=None) -> SectionProfile:
    input_curve = [
        (-sec.w_top / 2, sec.h / 2),
        (sec.w_top / 2, sec.h / 2),
        (sec.w_top / 2, -sec.h / 2),
        (-sec.w_top / 2, -sec.h / 2),
    ]
    outer_curve = build_joined(input_curve)
    return SectionProfile(
        sec,
        return_solid,
        outer_curve=outer_curve,
        disconnected=False,
    )


def channel(sec: Section, return_solid=None) -> SectionProfile:
    input_curve = [
        (sec.w_top, sec.h / 2 - sec.t_ftop),
        (sec.w_top, sec.h / 2),
        (0, sec.h / 2),
        (0, -sec.h / 2),
        (sec.w_top, -sec.h / 2),
        (sec.w_top, -sec.h / 2 + sec.t_fbtn),
        (sec.t_w, -sec.h / 2 + sec.t_fbtn),
        (sec.t_w, sec.h / 2 - sec.t_fbtn),
    ]
    outer_curve = build_joined(input_curve)
    return SectionProfile(
        sec,
        return_solid,
        outer_curve=outer_curve,
        disconnected=False,
    )
