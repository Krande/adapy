import os
from itertools import chain
from typing import List

import numpy as np

from ada.concepts.containers import Materials, Nodes
from ada.concepts.levels import FEM, Assembly, Part
from ada.concepts.points import Node
from ada.core.utils import Counter, roundoff
from ada.fem import Bc, Constraint, Elem, FemSet, Mass, Spring
from ada.fem.containers import FemElements
from ada.fem.io.utils import str_to_int
from ada.materials import Material
from ada.materials.metals import CarbonSteel

from . import cards
from .common import sesam_el_map

_counter_part_name = Counter(prefix="T")


def read_fem(assembly: Assembly, fem_file: os.PathLike, fem_name: str = None):
    """Import contents from a Sesam fem file into an assembly object"""

    print("Starting import of Sesam input file")
    part_name = next(_counter_part_name) if fem_name is None else fem_name
    with open(fem_file, "r") as d:
        part = read_sesam_fem(d.read(), part_name)

    assembly.add_part(part)


def read_sesam_fem(bulk_str, part_name) -> Part:
    """Reads the content string of a Sesam input file and converts it to FEM objects"""
    from .read_sections import get_sections

    part = Part(part_name)
    fem = part.fem

    fem.nodes = get_nodes(bulk_str, fem)
    fem.elements = get_elements(bulk_str, fem)
    fem.elements.build_sets()
    part._materials = get_materials(bulk_str, part)
    fem.sets = part.fem.sets + get_sets(bulk_str, fem)
    fem.sections = get_sections(bulk_str, fem)
    # part.fem._masses = get_mass(bulk_str, part.fem)
    fem.constraints += get_constraints(bulk_str, fem)
    fem.springs = get_springs(bulk_str, fem)
    fem.bcs += get_bcs(bulk_str, fem)

    print(8 * "-" + f'Imported "{fem.instance_name}"')
    return part


def sesam_eltype_2_general(eltyp):
    """
    Converts the numeric definition of elements in Sesam to a generalized element type form (ie. B31, S4, etc..)

    :param eltyp:
    :return: Generic element description
    """
    for ses, gen in sesam_el_map.items():
        if str_to_int(eltyp) == ses:
            return gen

    raise Exception("Currently unsupported eltype", eltyp)


def eltype_2_sesam(eltyp) -> int:
    for ses, gen in sesam_el_map.items():
        if eltyp == gen:
            return ses

    raise Exception("Currently unsupported eltype", eltyp)


def get_nodes(bulk_str: str, parent: FEM) -> Nodes:
    def get_node(m):
        d = m.groupdict()
        return Node(
            [float(d["x"]), float(d["y"]), float(d["z"])],
            int(float(d["id"])),
            parent=parent,
        )

    return Nodes(list(map(get_node, cards.re_gcoord_in.finditer(bulk_str))), parent=parent)


def get_elements(bulk_str: str, fem: FEM) -> FemElements:
    """Import elements from Sesam Bulk str"""

    def grab_elements(match):
        d = match.groupdict()
        nodes = [
            fem.nodes.from_id(x)
            for x in filter(
                lambda x: x != 0,
                map(str_to_int, d["nids"].replace("\n", "").split()),
            )
        ]
        eltyp = d["eltyp"]
        el_type = sesam_eltype_2_general(eltyp)
        metadata = dict(eltyad=str_to_int(d["eltyad"]), eltyp=eltyp)
        return Elem(
            str_to_int(d["elno"]),
            nodes,
            el_type,
            None,
            parent=fem,
            metadata=metadata,
        )

    return FemElements(list(map(grab_elements, cards.re_gelmnt.finditer(bulk_str))), fem_obj=fem)


def get_materials(bulk_str, part) -> Materials:
    """
    Interpret Material bulk string to FEM objects


    TDMATER: Material Element
    MISOSEL: linear elastic,isotropic

    TDMATER   4.00000000E+00  4.50000000E+01  1.07000000E+02  0.00000000E+00
            softMat

    MISOSEL   1.00000000E+00  2.10000003E+11  3.00000012E-01  1.15515586E+04
              1.14999998E+00  1.20000004E-05  1.00000000E+00  3.55000000E+08

    :return:
    """

    def grab_name(m):
        d = m.groupdict()
        return str_to_int(d["geo_no"]), d["name"]

    mat_names = {matid: mat_name for matid, mat_name in map(grab_name, cards.re_matnames.finditer(bulk_str))}

    def get_morsmel(m) -> Material:
        """
        MORSMEL

        Anisotropy, Linear Elastic Structural Analysis, 2-D Membrane Elements and 2-D Thin Shell Elements

        :param m:
        :return:
        """

        d = m.groupdict()
        matno = str_to_int(d["matno"])
        return Material(
            name=mat_names[matno],
            mat_id=matno,
            mat_model=CarbonSteel(
                rho=roundoff(d["rho"]),
                E=roundoff(d["d11"]),
                v=roundoff(d["ps1"]),
                alpha=roundoff(d["alpha1"]),
                zeta=roundoff(d["damp1"]),
                sig_p=[],
                eps_p=[],
                sig_y=5e6,
            ),
            metadata=d,
            parent=part,
        )

    def get_mat(match) -> Material:
        d = match.groupdict()
        matno = str_to_int(d["matno"])
        return Material(
            name=mat_names[matno],
            mat_id=matno,
            mat_model=CarbonSteel(
                rho=roundoff(d["rho"]),
                E=roundoff(d["young"]),
                v=roundoff(d["poiss"]),
                alpha=roundoff(d["damp"]),
                zeta=roundoff(d["alpha"]),
                sig_p=[],
                eps_p=[],
                sig_y=roundoff(d["yield"]),
            ),
            parent=part,
        )

    return Materials(
        chain(map(get_mat, cards.re_misosel.finditer(bulk_str)), map(get_morsmel, cards.re_morsmel.finditer(bulk_str))),
        parent=part,
    )


def get_sets(bulk_str, parent):
    from itertools import groupby
    from operator import itemgetter

    from ada.fem import FemSet
    from ada.fem.containers import FemSets

    def get_setmap(m):
        d = m.groupdict()
        set_type = "nset" if str_to_int(d["istype"]) == 1 else "elset"
        if set_type == "nset":
            members = [parent.nodes.from_id(str_to_int(x)) for x in d["members"].split()]
        else:
            members = [parent.elements.from_id(str_to_int(x)) for x in d["members"].split()]
        return str_to_int(d["isref"]), set_type, members

    set_map = dict()
    for setid_el_type, content in groupby(map(get_setmap, cards.re_setmembs.finditer(bulk_str)), key=itemgetter(0, 1)):
        setid = setid_el_type[0]
        eltype = setid_el_type[1]
        set_map[setid] = [list(), eltype]
        for c in content:
            set_map[setid][0] += c[2]

    def get_femsets(m):
        nonlocal set_map
        d = m.groupdict()
        isref = str_to_int(d["isref"])
        fem_set = FemSet(
            d["set_name"].strip(),
            set_map[isref][0],
            set_map[isref][1],
            parent=parent,
        )
        return fem_set

    return FemSets(list(map(get_femsets, cards.re_setnames.finditer(bulk_str))), fem_obj=parent)


def get_mass(bulk_str, fem):
    """

    :param bulk_str:
    :param fem:
    :type fem: ada.fem.FEM
    :return:
    """

    def checkEqual2(iterator):
        return len(set(iterator)) <= 1

    def grab_mass(match):
        d = match.groupdict()

        nodeno = str_to_int(d["nodeno"])
        mass_in = [
            roundoff(d["m1"]),
            roundoff(d["m2"]),
            roundoff(d["m3"]),
            roundoff(d["m4"]),
            roundoff(d["m5"]),
            roundoff(d["m6"]),
        ]
        masses = [m for m in mass_in if m != 0.0]
        if checkEqual2(masses):
            mass_type = None
            masses = [masses[0]] if len(masses) > 0 else [0.0]
        else:
            mass_type = "anisotropic"
        no = fem.nodes.from_id(nodeno)
        fem_set = FemSet(f"m{nodeno}", [], "elset", metadata=dict(internal=True), parent=fem)
        mass = Mass(f"m{nodeno}", fem_set, masses, "mass", ptype=mass_type, parent=fem)
        elem = Elem(no.id, [no], "mass", fem_set, mass_props=mass, parent=fem)
        fem.elements.add(elem)
        fem_set.add_members([elem])
        fem.sets.add(fem_set)
        return Mass(f"m{nodeno}", fem_set, masses, "mass", ptype=mass_type, parent=fem)

    return {m.name: m for m in map(grab_mass, cards.re_bnmass.finditer(bulk_str))}


def get_constraints(bulk_str, fem: FEM) -> List[Constraint]:
    def grab_constraint(master, data):
        m = str_to_int(master)
        m_set = FemSet(f"co{m}_m", [fem.nodes.from_id(m)], "nset")
        slaves = []
        for d in data:
            s = str_to_int(d["slave"])
            slaves.append(fem.nodes.from_id(s))
        s_set = FemSet(f"co{m}_m", slaves, "nset")
        fem.add_set(m_set)
        fem.add_set(s_set)
        return Constraint(f"co{m}", "coupling", m_set, s_set, parent=fem)

    con_map = [m.groupdict() for m in cards.re_bldep.finditer(bulk_str)]
    con_map.sort(key=lambda x: x["master"])
    from itertools import groupby

    return [grab_constraint(m, d) for m, d in groupby(con_map, key=lambda x: x["master"])]


def get_springs(bulk_str, fem: FEM):
    gr_spr_elements = None
    for eltype, elements in fem.elements.group_by_type():
        if eltype == "SPRING1":
            gr_spr_elements = {el.metadata["matno"]: el for el in elements}

    def grab_grspring(m):
        nonlocal gr_spr_elements
        d = m.groupdict()
        matno = str_to_int(d["matno"])
        ndof = str_to_int(d["ndof"])
        bulk = d["bulk"].replace("\n", "").split()
        el = gr_spr_elements[matno]
        spr_name = f"spr{el.id}"

        n1 = el.nodes[0]
        a = 1
        row = 0
        spring = []
        subspring = []
        for dof in bulk:
            subspring.append(float(dof.strip()))
            a += 1
            if a > ndof - row:
                spring.append(subspring)
                subspring = []
                a = 1
                row += 1
        new_s = []
        for row in spring:
            l = abs(len(row) - 6)
            if l > 0:
                new_s.append([0.0 for i in range(0, l)] + row)
            else:
                new_s.append(row)
        X = np.array(new_s)
        X = X + X.T - np.diag(np.diag(X))
        return Spring(spr_name, matno, "SPRING1", n1=n1, stiff=X, parent=fem)

    return {c.name: c for c in map(grab_grspring, cards.re_mgsprng.finditer(bulk_str))}


def get_bcs(bulk_str, fem: FEM) -> List[Bc]:
    def grab_bc(match) -> Bc:
        d = match.groupdict()
        node = fem.nodes.from_id(str_to_int(d["nodeno"]))
        assert isinstance(node, Node)

        fem_set = FemSet(f"bc{node.id}_set", [node], "nset")
        fem.sets.add(fem_set)
        dofs = []
        for i, c in enumerate(d["content"].replace("\n", "").split()):
            bc_sestype = str_to_int(c.strip())
            if bc_sestype in [0, 4]:
                continue
            dofs.append(i + 1)
        bc = Bc(f"bc{node.id}", fem_set, dofs, parent=fem)
        node.bc = bc

        return bc

    return list(map(grab_bc, cards.re_bnbcd.finditer(bulk_str)))
