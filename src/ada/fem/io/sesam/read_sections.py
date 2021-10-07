import logging
from itertools import chain, count

import numpy as np

from ada.concepts.containers import Sections
from ada.concepts.levels import FEM
from ada.concepts.structural import Section
from ada.core.utils import roundoff, unit_vector, vector_length
from ada.fem import Csys, FemSection, FemSet
from ada.fem.containers import FemSections
from ada.fem.io.utils import str_to_int
from ada.fem.shapes import ElemShapes, ElemType
from ada.sections import GeneralProperties

from . import cards


def get_sections(bulk_str, fem: FEM) -> FemSections:
    """
    GIORH (I-section)
    GUSYI (unsymm.I-section)
    GCHAN  (Channel section)
    GBOX (Box section)
    GPIPE (Pipe section)
    GBARM (Massive bar)
    GTONP (T on plate)
    GDOBO (Double box)
    GLSEC (L section)
    GIORHR
    GCHANR
    GLSECR
    """
    # Section Names
    sect_names = {sec_id: name for sec_id, name in map(get_section_names, cards.re_sectnames.finditer(bulk_str))}
    # Local Coordinate Systems
    lcsysd = {transno: vec for transno, vec in map(get_lcsys, cards.re_lcsys.finditer(bulk_str))}
    # Hinges
    hinges = {fixno: values for fixno, values in map(get_hinges, cards.re_belfix.finditer(bulk_str))}
    # Thickness'
    thick = {geono: t for geono, t in map(get_thicknesses, cards.re_thick.finditer(bulk_str))}
    # Eccentricities
    ecc = {eccno: values for eccno, values in map(get_eccentricities, cards.re_geccen.finditer(bulk_str))}

    list_of_sections = chain(
        (get_isection(m, sect_names, fem) for m in cards.re_giorh.finditer(bulk_str)),
        (get_box_section(m, sect_names, fem) for m in cards.re_gbox.finditer(bulk_str)),
        (get_tubular_section(m, sect_names, fem) for m in cards.re_gpipe.finditer(bulk_str)),
        (get_flatbar(m, sect_names, fem) for m in cards.re_gbarm.finditer(bulk_str)),
    )

    fem.parent._sections = Sections(list_of_sections)
    [add_general_sections(m, fem) for m in cards.re_gbeamg.finditer(bulk_str)]

    geom = count(1)
    total_geo = count(1)

    sections = filter(
        lambda x: x is not None,
        (get_femsecs(m, total_geo, geom, lcsysd, hinges, ecc, thick, fem) for m in cards.re_gelref1.finditer(bulk_str)),
    )
    fem_sections = FemSections(sections, fem_obj=fem)
    logging.info(f"Successfully imported {next(geom) - 1} FEM sections out of {next(total_geo) - 1}")
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
        genprops=GeneralProperties(sfy=float(d["sfy"]), sfz=float(d["sfz"])),
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
        genprops=GeneralProperties(sfy=float(d["sfy"]), sfz=float(d["sfz"])),
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
        genprops=GeneralProperties(sfy=float(d["sfy"]), sfz=float(d["sfz"])),
        parent=fem.parent,
    )


def add_general_sections(match, fem) -> None:
    d = match.groupdict()
    sec_id = str_to_int(d["geono"])
    gen_props = GeneralProperties(
        ax=roundoff(d["area"], 10),
        ix=roundoff(d["ix"], 10),
        iy=roundoff(d["iy"], 10),
        iz=roundoff(d["iz"], 10),
        iyz=roundoff(d["iyz"], 10),
        wxmin=roundoff(d["wxmin"]),
        wymin=roundoff(d["wymin"]),
        wzmin=roundoff(d["wzmin"]),
        shary=roundoff(d["shary"]),
        sharz=roundoff(d["sharz"]),
        scheny=roundoff(d["shceny"]),
        schenz=roundoff(d["shcenz"]),
        sy=float(d["sy"]),
        sz=float(d["sz"]),
    )
    if sec_id in fem.parent.sections.idmap.keys():
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
        genprops=GeneralProperties(sfy=float(d["sfy"]), sfz=float(d["sfz"])),
        parent=fem.parent,
    )


def get_femsecs(match, total_geo, importedgeom_counter, lcsysd, hinges_global, eccentricities, thicknesses, fem):
    d = match.groupdict()
    geono = str_to_int(d["geono"])
    next(total_geo)
    transno = str_to_int(d["transno"])
    elno = str_to_int(d["elno"])
    elem = fem.elements.from_id(elno)

    matno = str_to_int(d["matno"])

    # Go no further if element has no fem section
    if elem.type in ElemShapes.springs + ElemShapes.masses:
        next(importedgeom_counter)
        elem.metadata["matno"] = matno
        return None

    mat = fem.parent.materials.get_by_id(matno)
    if elem.type in ElemShapes.lines:
        next(importedgeom_counter)
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

        hinges = None
        if fix_data == -1:
            hinges = get_hinges_from_elem(elem, members, hinges_global, lcsysd, xvec, zvec, yvec)

        offset = None
        if ecc_data == -1:
            offset = get_ecc_from_elem(elem, members, eccentricities, fix_data)

        fem_set = FemSet(sec.name, [elem], "elset", metadata=dict(internal=True), parent=fem)
        fem.sets.add(fem_set, append_suffix_on_exist=True)
        fem_sec = FemSection(
            name=sec.name,
            sec_type=ElemType.LINE,
            elset=fem_set,
            section=sec,
            local_z=zvec,
            local_y=yvec,
            material=mat,
            offset=offset,
            hinges=hinges,
            parent=fem,
        )
        return fem_sec

    elif elem.type in ElemShapes.shell:
        next(importedgeom_counter)
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
        logging.debug(e)
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


def get_hinges_from_elem(elem, members, hinges_global, lcsysd, xvec, zvec, yvec):
    """

    :param elem:
    :param members:
    :param hinges_global:
    :type elem: ada.Elem
    :return:
    """
    if len(elem.nodes) > 2:
        raise ValueError("This algorithm was not designed for more than 2 noded elements")
    from ada.core.utils import unit_vector

    hinges = []
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
        d = [int(x) for x, i in zip(dofs_origin, (a1, a2, a3, a4, a5, a6)) if int(i) != 0]

        hinges.append((n, d, csys))
    return hinges


def get_ecc_from_elem(elem, members, eccentricities, fix_data):
    """

    :param elem:
    :param members:
    :param eccentricities:
    :param fix_data:
    :type elem: ada.fem.Elem
    """
    # To the interpretation here
    start = 0 if fix_data != -1 else len(elem.nodes)
    end = len(elem.nodes) if fix_data != -1 else 2 * len(elem.nodes)
    eccen = []
    for i, x in enumerate(members[start:]):
        if i >= end:
            break
        if x == 0:
            continue
        n_offset = elem.nodes[i]
        ecc = eccentricities[x]
        eccen.append((n_offset, ecc))
    return eccen


def get_lcsys(m):
    d = m.groupdict()
    return str_to_int(d["transno"]), (
        roundoff(d["unix"]),
        roundoff(d["uniy"]),
        roundoff(d["uniz"]),
    )
