from itertools import chain, count
from typing import Union

import numpy as np

from ada.api.containers import Sections
from ada.config import logger
from ada.core.utils import roundoff
from ada.core.vector_utils import unit_vector, vector_length
from ada.fem import FEM, Csys, Elem, FemSection, FemSet
from ada.fem.containers import FemSections
from ada.fem.formats.utils import str_to_int
from ada.fem.shapes import ElemType
from ada.fem.shapes import definitions as shape_def
from ada.materials import Material
from ada.sections import GeneralProperties
from ada.sections.concept import Section

from . import cards


def get_sections(bulk_str, fem: FEM, mass_elem, spring_elem) -> FemSections:
    # Section Names
    sect_names = {sec_id: name for sec_id, name in map(get_section_names, cards.re_sectnames.finditer(bulk_str))}
    # Local Coordinate Systems
    lcsysd = {transno: vec for transno, vec in map(get_lcsys, cards.GUNIVEC.to_ff_re().finditer(bulk_str))}
    # Hinges
    hinges = {fixno: values for fixno, values in map(get_hinges, cards.re_belfix.finditer(bulk_str))}
    # Thickness'
    thick = {geono: t for geono, t in map(get_thicknesses, cards.GELTH.to_ff_re().finditer(bulk_str))}
    # Eccentricities
    ecc = {eccno: values for eccno, values in map(get_eccentricities, cards.re_geccen.finditer(bulk_str))}

    list_of_sections = chain(
        (get_isection(m, sect_names, fem) for m in cards.GIORH.to_ff_re().finditer(bulk_str)),
        (get_box_section(m, sect_names, fem) for m in cards.GBOX.to_ff_re().finditer(bulk_str)),
        (get_tubular_section(m, sect_names, fem) for m in cards.re_gpipe.finditer(bulk_str)),
        (get_flatbar(m, sect_names, fem) for m in cards.re_gbarm.finditer(bulk_str)),
    )

    fem.parent._sections = Sections(list_of_sections, parent=fem.parent)
    [add_general_sections(m, fem) for m in cards.re_gbeamg.finditer(bulk_str)]

    geom = count(1)
    total_geo = count(1)
    res = (
        get_femsecs(m, total_geo, geom, lcsysd, hinges, ecc, thick, fem, mass_elem, spring_elem)
        for m in cards.GELREF1.to_ff_re().finditer(bulk_str)
    )
    sections = filter(lambda x: type(x) is FemSection, res)

    fem_sections = FemSections(sections, fem_obj=fem)
    logger.info(f"Successfully imported {next(geom) - 1} FEM sections out of {next(total_geo) - 1}")
    return fem_sections


def get_isection(match, sect_names, fem) -> Section:
    d = match.groupdict()
    sec_id = str_to_int(d["geono"])
    name = sect_names[sec_id]
    return Section(
        name=name,
        sec_id=sec_id,
        sec_type=Section.TYPES.IPROFILE,
        h=roundoff(d["hz"]),
        t_w=roundoff(d["ty"]),
        w_top=roundoff(d["bt"]),
        w_btn=roundoff(d["bb"]),
        t_ftop=roundoff(d["tt"]),
        t_fbtn=roundoff(d["tb"]),
        parent=fem.parent,
    )


def get_box_section(match, sect_names, fem) -> Section:
    d = match.groupdict()
    sec_id = str_to_int(d["geono"])
    return Section(
        name=sect_names[sec_id],
        sec_id=sec_id,
        sec_type=Section.TYPES.BOX,
        h=roundoff(d["hz"]),
        w_top=roundoff(d["by"]),
        w_btn=roundoff(d["by"]),
        t_w=roundoff(d["ty"]),
        t_ftop=roundoff(d["tt"]),
        t_fbtn=roundoff(d["tb"]),
        parent=fem.parent,
    )


def get_flatbar(match, sect_names, fem) -> Section:
    d = match.groupdict()
    sec_id = str_to_int(d["geono"])
    return Section(
        name=sect_names[sec_id],
        sec_id=sec_id,
        sec_type=Section.TYPES.FLATBAR,
        h=roundoff(d["hz"]),
        w_top=roundoff(d["bt"]),
        w_btn=roundoff(d["bb"]),
        parent=fem.parent,
    )


def add_general_sections(match, fem) -> None:
    d = match.groupdict()
    sec_id = str_to_int(d["geono"])
    gen_props = GeneralProperties(
        Ax=roundoff(d["area"], 10),
        Ix=roundoff(d["ix"], 10),
        Iy=roundoff(d["iy"], 10),
        Iz=roundoff(d["iz"], 10),
        Iyz=roundoff(d["iyz"], 10),
        Wxmin=roundoff(d["wxmin"]),
        Wymin=roundoff(d["wymin"]),
        Wzmin=roundoff(d["wzmin"]),
        Shary=roundoff(d["shary"]),
        Sharz=roundoff(d["sharz"]),
        Shceny=roundoff(d["shceny"]),
        Shcenz=roundoff(d["shcenz"]),
        Sy=float(d["sy"]),
        Sz=float(d["sz"]),
    )

    if sec_id in fem.parent.sections.id_map.keys():
        sec = fem.parent.sections.get_by_id(sec_id)
        sec._genprops = gen_props
        gen_props.parent = sec
    else:
        stype = Section.TYPES.GENERAL
        sec = Section(name=f"GB{sec_id}", sec_id=sec_id, sec_type=stype, genprops=gen_props, parent=fem.parent)
        gen_props.parent = sec
        fem.parent.sections.add(sec)


def get_tubular_section(match, sect_names, fem) -> Section:
    d = match.groupdict()
    sec_id = str_to_int(d["geono"])
    if sec_id not in sect_names:
        sec_name = f"TUB{sec_id}"
    else:
        sec_name = sect_names[sec_id]
    t = d["t"] if d["t"] is not None else (d["dy"] - d["di"]) / 2
    return Section(
        name=sec_name,
        sec_id=sec_id,
        sec_type=Section.TYPES.TUBULAR,
        r=roundoff(float(d["dy"]) / 2),
        wt=roundoff(t),
        parent=fem.parent,
    )


def read_line_section(elem: Elem, fem: FEM, mat: Material, geono, d, lcsysd, hinges_global, eccentricities):
    transno = str_to_int(d["transno"])
    sec = fem.parent.sections.get_by_id(geono)
    n1, n2 = elem.nodes
    v = n2.p - n1.p
    if vector_length(v) == 0.0:
        xvec = [1, 0, 0]
    else:
        xvec = unit_vector(v)
    zvec = lcsysd[transno]
    crossed = np.cross(zvec, xvec)
    ma = max(abs(crossed))
    yvec = tuple([roundoff(x / ma, 3) for x in crossed])

    fix_data = str_to_int(d["fixno"])
    ecc_data = str_to_int(d["eccno"])

    members = None
    if d["members"] is not None:
        members = [str_to_int(x) for x in d["members"].replace("\n", " ").split()]

    if fix_data == -1:
        add_hinge_prop_to_elem(elem, members, hinges_global, xvec, yvec)

    if ecc_data == -1:
        add_ecc_to_elem(elem, members, eccentricities, fix_data)

    fem_set = FemSet(sec.name, [elem], "elset", metadata=dict(internal=True), parent=fem)
    fem.sets.add(fem_set, append_suffix_on_exist=True)
    fem_sec = FemSection(
        sec_id=geono,
        name=sec.name,
        sec_type=ElemType.LINE,
        elset=fem_set,
        section=sec,
        local_z=zvec,
        local_y=yvec,
        material=mat,
        parent=fem,
    )
    return fem_sec


def read_shell_section(elem: Elem, fem: FEM, mat: Material, elno, thicknesses, geono):
    sec_name = f"sh{elno}"
    fem_set = FemSet(sec_name, [elem], "elset", parent=fem, metadata=dict(internal=True))
    fem.sets.add(fem_set)
    fem_sec = FemSection(
        name=sec_name,
        sec_type=ElemType.SHELL,
        thickness=roundoff(thicknesses[geono]),
        elset=fem_set,
        material=mat,
        parent=fem,
    )
    return fem_sec


def get_femsecs(match, total_geo, curr_geom_num, lcsysd, hinges_global, ecc, thicknesses, fem, mass_elem, spring_elem):
    next(total_geo)
    d = match.groupdict()
    geono = str_to_int(d["geono"])
    elno = str_to_int(d["elno"])
    matno = str_to_int(d["matno"])

    # Go no further if element has no fem section
    if elno in spring_elem.keys():
        next(curr_geom_num)
        spring_elem[elno]["section_data"] = d
        return None

    if elno in mass_elem.keys():
        next(curr_geom_num)
        mass_elem[elno]["section_data"] = d
        return None

    elem = fem.elements.from_id(elno)
    mat = fem.parent.materials.get_by_id(matno)
    if isinstance(elem.type, shape_def.LineShapes):
        next(curr_geom_num)
        return read_line_section(elem, fem, mat, geono, d, lcsysd, hinges_global, ecc)
    elif isinstance(elem.type, shape_def.ShellShapes):
        next(curr_geom_num)
        return read_shell_section(elem, fem, mat, elno, thicknesses, geono)
    else:
        raise ValueError("Section not added to conversion")


def get_thicknesses(match):
    d = match.groupdict()
    sec_id = str_to_int(d["geono"])
    t = d["th"]
    return sec_id, t


def get_hinges(match):
    d = match.groupdict()
    fixno = str_to_int(d["fixno"])
    opt = str_to_int(d["opt"])
    trano = str_to_int(d["trano"])
    a1 = str_to_int(d["a1"])
    a2 = str_to_int(d["a2"])
    a3 = str_to_int(d["a3"])
    a4 = str_to_int(d["a4"])
    a5 = str_to_int(d["a5"])
    try:
        a6 = str_to_int(d["a6"])
    except BaseException as e:
        logger.debug(e)
        a6 = 0
        pass
    return fixno, (opt, trano, a1, a2, a3, a4, a5, a6)


def get_eccentricities(match):
    d = match.groupdict()
    eccno = str_to_int(d["eccno"])
    ex = float(d["ex"])
    ey = float(d["ey"])
    ez = float(d["ez"])
    return eccno, (ex, ey, ez)


def get_section_names(m):
    d = m.groupdict()
    return str_to_int(d["geono"]), d["set_name"].strip()


def add_hinge_prop_to_elem(elem: Elem, members, hinges_global, xvec, yvec) -> None:
    """Add hinge property to element from sesam FEM file"""
    from ada.fem.elements import Hinge, HingeProp

    if len(elem.nodes) > 2:
        raise ValueError("This algorithm was not designed for more than 2 noded elements")

    for i, x in enumerate(members):
        if i >= len(elem.nodes):
            break
        if x == 0:
            continue
        if x not in hinges_global.keys():
            raise ValueError("fixno not found!")
        opt, trano, a1, a2, a3, a4, a5, a6 = hinges_global[x]
        n = elem.nodes[i]
        if trano > 0:
            csys = None
        else:
            csys = Csys(
                f"el{elem.id}_hinge{i + 1}_csys",
                coords=([unit_vector(xvec) + n.p, unit_vector(yvec) + n.p, n.p]),
                parent=elem.parent,
            )
        dofs_origin = [1, 2, 3, 4, 5, 6]
        dofs = [int(x) for x, i in zip(dofs_origin, (a1, a2, a3, a4, a5, a6)) if int(i) != 0]
        end = Hinge(retained_dofs=dofs, csys=csys, fem_node=n)
        if i == 0:
            elem.hinge_prop = HingeProp(end1=end)
        else:
            elem.hinge_prop = HingeProp(end2=end)


def add_ecc_to_elem(elem: Elem, members, eccentricities, fix_data) -> None:
    """Adds eccentricity to element from sesam FEM file"""
    from ada.fem.elements import Eccentricity, EccPoint

    # To the interpretation here
    start = 0 if fix_data != -1 else len(elem.nodes)
    end = len(elem.nodes) - 1 if fix_data != -1 else 2 * len(elem.nodes)
    end1: Union[None, EccPoint] = None
    end2: Union[None, EccPoint] = None
    for i, x in enumerate(members[start:]):
        if i > end:
            break
        if x == 0:
            continue
        n_offset = elem.nodes[i]
        ecc = eccentricities[x]
        if i == 0:
            end1 = EccPoint(n_offset, ecc)
        if i == end:
            end2 = EccPoint(n_offset, ecc)

    elem.eccentricity = Eccentricity(end1, end2)


def get_lcsys(m):
    d = m.groupdict()
    return str_to_int(d["transno"]), (
        roundoff(d["unix"]),
        roundoff(d["uniy"]),
        roundoff(d["uniz"]),
    )
