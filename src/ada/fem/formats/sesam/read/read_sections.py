from itertools import chain
from typing import Union

import numpy as np

from ada.api.containers import Sections
from ada.config import logger
from ada.core.vector_utils import unit_vector, vector_length
from ada.fem import FEM, Csys, Elem, FemSection, FemSet
from ada.fem.containers import FemSections
from ada.fem.formats.utils import str_to_int
from ada.fem.shapes import ElemType
from ada.fem.shapes import definitions as shape_def
from ada.sections import GeneralProperties
from ada.sections.concept import Section
from ada.sections.properties import normalize_general_properties

from . import cards


def get_elrefs(bulk_str, mass_elem: dict, spring_elem: dict) -> dict[int, dict]:
    """GELREF1 per structural element, ``{elno: record}``.

    A spring's or mass element's record is handed to it instead (``section_data``): it carries
    the MGSPRNG/MGMASS number, not a section. Split out of :func:`get_sections` so the springs,
    masses and sets can be read before the sections are, which need the sets.
    """
    elrefs = {}
    for m in cards.GELREF1.to_ff_re().finditer(bulk_str):
        d = m.groupdict()
        elno = str_to_int(d["elno"])
        if elno in spring_elem:
            spring_elem[elno]["section_data"] = d
        elif elno in mass_elem:
            mass_elem[elno]["section_data"] = d
        else:
            elrefs[elno] = d
    return elrefs


def get_sections(bulk_str, fem: FEM, elrefs: dict[int, dict]) -> FemSections:
    """The FemSections, one per element set the deck assigns properties to.

    GELREF1 gives every element its own material, geometry (GELTH / beam profile) and
    orientation; adapy groups elements under a section. They used to come back one section
    (and one generated set) per element -- ``sh1``, ``sh2``, ... -- so a model's sections, and
    every element's reference to one, changed on the way through a Sesam file. Now:

    1. A set whose TDSETNAM carries a ``SECTION: <name>`` comment (``write_sets.SECTION_TAG``,
       what adapy's writer puts on a section's set) is that section, by that name, when its
       members share one material, geometry and orientation.
    2. The remaining elements are grouped by the GELREF1 data they share, in element order. A
       named element set holding exactly a group is that group's set; otherwise the group gets
       a generated internal set, named as before after its first element (or its profile).

    ``fem.sets`` must already hold the deck's sets.
    """
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
        (get_angular_section(m, sect_names, fem) for m in cards.GLSEC.to_ff_re().finditer(bulk_str)),
        (get_flatbar(m, sect_names, fem) for m in cards.re_gbarm.finditer(bulk_str)),
    )

    fem.parent._sections = Sections(list_of_sections, parent=fem.parent)
    [add_general_sections(m, fem) for m in cards.re_gbeamg.finditer(bulk_str)]

    builder = _SectionBuilder(fem, elrefs, lcsysd, hinges, ecc, thick)
    fem_sections = FemSections(builder.build(section_sets(bulk_str, fem)), fem_obj=fem)
    logger.info(f"Successfully imported {len(fem_sections)} FEM sections for {len(elrefs)} elements")
    return fem_sections


def section_sets(bulk_str, fem: FEM) -> list[tuple[FemSet, str]]:
    """``(element set, section name)`` for every TDSETNAM carrying a section comment."""
    from ..write.write_sets import SECTION_TAG
    from .read_sets import text_record

    out = []
    for m in cards.re_setnames.finditer(bulk_str):
        set_name, comments = text_record(m.groupdict(), "set_name")
        tagged = [c[len(SECTION_TAG) :].strip() for c in comments if c.startswith(SECTION_TAG)]
        if not tagged or set_name not in fem.sets.elements:
            continue
        out.append((fem.sets.get_elset_from_name(set_name), tagged[0]))
    return out


class _SectionBuilder:
    """Builds the FemSections of :func:`get_sections` from the per-element GELREF1 records."""

    def __init__(self, fem: FEM, elrefs, lcsysd, hinges, ecc, thick):
        self.fem = fem
        self.elrefs = elrefs
        self.lcsysd = lcsysd
        self.hinges = hinges
        self.ecc = ecc
        self.thick = thick
        self._names: set[str] = set()
        self._set_names: set[str] = {fs.name for fs in fem.sets.sets}

    def key(self, elno: int, orientation: bool) -> tuple:
        """What an element's section is made of. ``orientation`` adds a beam's own y axis,
        which follows from its direction and so differs between beams sharing a GUNIVEC."""
        d = self.elrefs[elno]
        elem = self.fem.elements.from_id(elno)
        matno, geono = str_to_int(d["matno"]), str_to_int(d["geono"])
        if isinstance(elem.type, shape_def.LineShapes):
            transno = str_to_int(d["transno"])
            if not orientation:
                return ElemType.LINE, matno, geono, transno
            return ElemType.LINE, matno, geono, transno, tuple(self._local_y(elem, transno))
        if isinstance(elem.type, shape_def.ShellShapes):
            return ElemType.SHELL, matno, geono
        if isinstance(elem.type, shape_def.SolidShapes):
            return ElemType.SOLID, matno
        raise ValueError(f"Section not added to conversion: element {elno} of type {elem.type}")

    def _local_y(self, elem: Elem, transno: int) -> np.ndarray:
        # The beam's y axis is GUNIVEC's z crossed with the beam axis. It used to be scaled by
        # its largest component and rounded to 3 decimals, which is only a unit vector for an
        # axis-parallel beam.
        n1, n2 = elem.nodes[0], elem.nodes[-1]
        v = n2.p - n1.p
        xvec = np.array([1.0, 0.0, 0.0]) if vector_length(v) == 0.0 else unit_vector(v)
        return unit_vector(np.cross(self.lcsysd[transno], xvec))

    def build(self, tagged: list[tuple[FemSet, str]]) -> list[FemSection]:
        sections = []
        assigned: set[int] = set()
        used_sets: set[int] = set()
        for fs, name in tagged:
            ids = [m.id for m in fs.members]
            if not ids or any(i not in self.elrefs or i in assigned for i in ids):
                continue
            if len({self.key(i, orientation=False) for i in ids}) != 1:
                logger.warning(f'Set "{fs.name}" names section "{name}", but its elements do not share one section')
                continue
            sections.append(self.section(fs, self._unique(name), ids))
            assigned.update(ids)
            used_sets.add(id(fs))

        groups: dict[tuple, list[int]] = {}
        for elno in sorted(self.elrefs):
            if elno not in assigned:
                groups.setdefault(self.key(elno, orientation=True), []).append(elno)

        exact: dict[frozenset, FemSet] = {}
        for fs in self.fem.sets.elements.values():
            exact.setdefault(frozenset(m.id for m in fs.members), fs)

        for ids in groups.values():
            fs = exact.get(frozenset(ids))
            generated = self._generated_name(ids)
            if fs is None or id(fs) in used_sets:
                fs = FemSet(self._unique_set(generated), [], "elset", parent=self.fem, metadata=dict(internal=True))
                fs.add_members([self.fem.elements.from_id(i) for i in ids])
                self.fem.sets.add(fs)
            used_sets.add(id(fs))
            sections.append(self.section(fs, self._unique(generated), ids))
        return sections

    def _generated_name(self, ids: list[int]) -> str:
        elem = self.fem.elements.from_id(ids[0])
        if isinstance(elem.type, shape_def.LineShapes):
            return self.fem.parent.sections.get_by_id(str_to_int(self.elrefs[ids[0]]["geono"])).name
        if isinstance(elem.type, shape_def.ShellShapes):
            return f"sh{ids[0]}"
        return f"so{ids[0]}"

    def _unique(self, name: str) -> str:
        out, n = name, 0
        while out in self._names:
            n += 1
            out = f"{name}_{n}"
        self._names.add(out)
        return out

    def _unique_set(self, name: str) -> str:
        out, n = name, 0
        while out in self._set_names:
            n += 1
            out = f"{name}_{n}"
        self._set_names.add(out)
        return out

    def section(self, fs: FemSet, name: str, ids: list[int]) -> FemSection:
        d = self.elrefs[ids[0]]
        elem = self.fem.elements.from_id(ids[0])
        mat = self.fem.parent.materials.get_by_id(str_to_int(d["matno"]))
        geono = str_to_int(d["geono"])
        if isinstance(elem.type, shape_def.LineShapes):
            for i in ids:
                read_line_element(self.fem.elements.from_id(i), self.elrefs[i], self.hinges, self.ecc, self.lcsysd)
            transno = str_to_int(d["transno"])
            return FemSection(
                name=name,
                sec_type=ElemType.LINE,
                elset=fs,
                section=self.fem.parent.sections.get_by_id(geono),
                local_z=self.lcsysd[transno],
                local_y=self._local_y(elem, transno),
                material=mat,
                parent=self.fem,
            )
        if isinstance(elem.type, shape_def.ShellShapes):
            return FemSection(
                name=name,
                sec_type=ElemType.SHELL,
                thickness=float(self.thick[geono]),
                elset=fs,
                material=mat,
                parent=self.fem,
            )
        # Solid (continuum) elements have no geometric cross-section -- the GELREF1 record
        # only binds a material (geono=0).
        return FemSection(name=name, sec_type=ElemType.SOLID, elset=fs, material=mat, parent=self.fem)


def get_isection(match, sect_names, fem) -> Section:
    d = match.groupdict()
    sec_id = str_to_int(d["geono"])
    name = sect_names[sec_id]
    return Section(
        name=name,
        sec_id=sec_id,
        sec_type=Section.TYPES.IPROFILE,
        h=float(d["hz"]),
        t_w=float(d["ty"]),
        w_top=float(d["bt"]),
        w_btn=float(d["bb"]),
        t_ftop=float(d["tt"]),
        t_fbtn=float(d["tb"]),
        parent=fem.parent,
    )


def get_box_section(match, sect_names, fem) -> Section:
    d = match.groupdict()
    sec_id = str_to_int(d["geono"])
    return Section(
        name=sect_names[sec_id],
        sec_id=sec_id,
        sec_type=Section.TYPES.BOX,
        h=float(d["hz"]),
        w_top=float(d["by"]),
        w_btn=float(d["by"]),
        t_w=float(d["ty"]),
        t_ftop=float(d["tt"]),
        t_fbtn=float(d["tb"]),
        parent=fem.parent,
    )


def get_angular_section(match, sect_names, fem) -> Section:
    d = match.groupdict()
    sec_id = str_to_int(d["geono"])
    return Section(
        name=sect_names[sec_id],
        sec_id=sec_id,
        sec_type=Section.TYPES.ANGULAR,
        h=float(d["hz"]),
        w_top=float(d["by"]),
        w_btn=float(d["by"]),
        t_w=float(d["ty"]),
        t_ftop=float(d["tz"]),
        t_fbtn=float(d["tz"]),
        parent=fem.parent,
    )


def get_flatbar(match, sect_names, fem) -> Section:
    d = match.groupdict()
    sec_id = str_to_int(d["geono"])
    return Section(
        name=sect_names[sec_id],
        sec_id=sec_id,
        sec_type=Section.TYPES.FLATBAR,
        h=float(d["hz"]),
        w_top=float(d["bt"]),
        w_btn=float(d["bb"]),
        parent=fem.parent,
    )


def add_general_sections(match, fem) -> None:
    d = match.groupdict()
    sec_id = str_to_int(d["geono"])
    gen_props = GeneralProperties(
        Ax=float(d["area"]),
        Ix=float(d["ix"]),
        Iy=float(d["iy"]),
        Iz=float(d["iz"]),
        Iyz=float(d["iyz"]),
        Wxmin=float(d["wxmin"]),
        Wymin=float(d["wymin"]),
        Wzmin=float(d["wzmin"]),
        Shary=float(d["shary"]),
        Sharz=float(d["sharz"]),
        Shceny=float(d["shceny"]),
        Shcenz=float(d["shcenz"]),
        Sy=float(d["sy"]),
        Sz=float(d["sz"]),
    )

    if sec_id in fem.parent.sections.id_map.keys():
        sec = fem.parent.sections.get_by_id(sec_id)
        gen_props.parent = sec
        sec._genprops = normalize_general_properties(sec, gen_props)
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
    t = float(d["t"]) if d["t"] is not None else (float(d["dy"]) - float(d["di"])) / 2
    return Section(
        name=sec_name,
        sec_id=sec_id,
        sec_type=Section.TYPES.TUBULAR,
        r=float(d["dy"]) / 2,
        wt=t,
        parent=fem.parent,
    )


def read_line_element(elem: Elem, d: dict, hinges_global, eccentricities, lcsysd) -> None:
    """A beam element's own GELREF1 data: its end releases (BELFIX) and eccentricities."""
    n1, n2 = elem.nodes[0], elem.nodes[-1]
    v = n2.p - n1.p
    xvec = [1, 0, 0] if vector_length(v) == 0.0 else unit_vector(v)
    transno = str_to_int(d["transno"])
    yvec = unit_vector(np.cross(lcsysd[transno], xvec))

    fix_data = str_to_int(d["fixno"])
    ecc_data = str_to_int(d["eccno"])

    members = None
    if d["members"] is not None:
        members = [str_to_int(x) for x in d["members"].replace("\n", " ").split()]

    if fix_data == -1:
        add_hinge_prop_to_elem(elem, members, hinges_global, xvec, yvec)

    if ecc_data == -1:  # Eccentricity per node
        add_ecc_to_elem(elem, members, eccentricities, fix_data)
    elif ecc_data > 0:  # Constant eccentricity
        from ada.fem.elements import Eccentricity, EccPoint

        ecc = eccentricities[ecc_data]
        end1 = EccPoint(elem.nodes[0], ecc)
        end2 = EccPoint(elem.nodes[1], ecc)
        elem.eccentricity = Eccentricity(end1, end2)


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
    from .read_sets import text_record

    d = m.groupdict()
    return str_to_int(d["geono"]), text_record(d, "set_name")[0]


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
        float(d["unix"]),
        float(d["uniy"]),
        float(d["uniz"]),
    )
