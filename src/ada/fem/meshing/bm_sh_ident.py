from ada import Beam
from ada.core.utils import vector_length


def get_thick_normal_from_ig_beams(beam: Beam, cog, tol):
    t, n, c = None, None, None
    xdir, ydir, zdir = beam.ori

    n1 = beam.n1.p
    n2 = beam.n2.p
    h = beam.section.h
    w_btn = beam.section.w_btn
    w_top = beam.section.w_top

    p11 = n1 + zdir * h / 2
    p12 = p11 + ydir * w_top / 2
    p21 = n2 + zdir * h / 2
    p22 = p21 + ydir * w_top / 2

    p11btn = n1 - zdir * h / 2 + ydir * w_btn / 2
    p12btn = p11btn - ydir * w_top / 2
    p21btn = n2 - zdir * h / 2
    p22btn = p21btn + ydir * w_btn / 2

    web = (n1 + n2) / 2

    fl_top_right = (p11 + p12 + p21 + p22) / 4
    fl_top_left = fl_top_right - ydir * w_top / 2
    fl_btn_right = (p11btn + p12btn + p21btn + p22btn) / 4
    fl_btn_left = fl_btn_right - ydir * w_btn / 2

    if vector_length(web - cog) < tol:
        t, n, c = beam.section.t_w, ydir, "web"

    for x in [fl_top_right, fl_top_left]:
        if vector_length(x - cog) < tol:
            t, n, c = beam.section.t_ftop, zdir, "top_fl"

    for x in [fl_btn_right, fl_btn_left]:
        if vector_length(x - cog) < tol:
            t, n, c = beam.section.t_fbtn, zdir, "btn_fl"

    if t is None:
        raise ValueError("The thickness is not valid")

    return t, n, c


def get_thick_normal_from_angular_beams(beam: Beam, cog, tol):
    section_profile = beam.section.get_section_profile(False)
    print(section_profile)
    raise NotImplementedError()


def eval_thick_normal_from_cog_of_beam_plate(beam: Beam, cog):
    from ada.sections import SectionCat

    if SectionCat.is_circular_profile(beam.section.type) or SectionCat.is_tubular_profile(beam.section.type):
        tol = beam.section.r / 8
    else:
        tol = beam.section.h / 8

    t, n, c = None, None, None
    if beam.section.type in SectionCat.iprofiles + SectionCat.igirders:
        t, n, c = get_thick_normal_from_ig_beams(beam, cog, tol)
    elif SectionCat.is_angular(beam.section):
        t, n, c = get_thick_normal_from_angular_beams(beam, cog, tol)
    else:
        raise NotImplementedError("Not yet supported ")
    return t, n, c
