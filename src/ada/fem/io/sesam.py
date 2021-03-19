import logging
import os
import re
from itertools import chain

import numpy as np

from ada import Assembly, Material, Part, Section
from ada.core.containers import Materials
from ada.fem import Constraint, Csys, Elem, FemSection, FemSet, Load, Mass, Spring
from ada.fem.io import FemObjectReader, FemWriter
from ada.materials.metals import CarbonSteel
from ada.sections import GeneralProperties


def read_fem(assembly, fem_file, fem_name=None):
    """
    Import contents from a Sesam fem file into an assembly object

    :param assembly: An Assembly object
    :param fem_file: A Sesam fem file
    :param fem_name: The desired name of the part generated from the fem file (optional).
    :type fem_file: pathlib.Path
    """
    print("Starting import of Sesam input file")
    part_name = "T1" if fem_name is None else fem_name
    reader = SesamReader(assembly, part_name)
    with open(fem_file, "r") as d:
        reader.read_sesam_fem(d.read())


def to_fem(
    assembly,
    name,
    scratch_dir=None,
    metadata=None,
    execute=False,
    run_ext=False,
    cpus=2,
    gpus=None,
    overwrite=False,
    exit_on_complete=False,
):
    if metadata is None:
        metadata = dict()
    if "control_file" not in metadata.keys():
        metadata["control_file"] = None

    a = SesamWriter(assembly)
    a.write(name, scratch_dir, metadata, overwrite=overwrite)


class SesamReader(FemObjectReader):
    re_in = re.IGNORECASE | re.MULTILINE | re.DOTALL

    # Nodes
    re_gnode_in = FemObjectReader.get_ff_regex("GNODE", "nodex", "nodeno", "ndof", "odof")
    re_gcoord_in = FemObjectReader.get_ff_regex("GCOORD", "id", "x", "y", "z")

    # Elements
    re_gelmnt = FemObjectReader.get_ff_regex("GELMNT1", "elnox", "elno", "eltyp", "eltyad", "nids")
    re_gelref1 = FemObjectReader.get_ff_regex(
        "GELREF1",
        "elno",
        "matno",
        "addno",
        "intno",
        "mintno",
        "strano",
        "streno",
        "strepono",
        "geono",
        "fixno",
        "eccno",
        "transno",
        "members|",
    )

    # Beam Sections
    re_sectnames = FemObjectReader.get_ff_regex("TDSECT", "nfield", "geono", "codnam", "codtxt", "set_name")
    re_giorh = FemObjectReader.get_ff_regex(
        "GIORH ",
        "geono",
        "hz",
        "ty",
        "bt",
        "tt",
        "bb",
        "tb",
        "sfy",
        "sfz",
        "NLOBYT|",
        "NLOBYB|",
        "NLOBZ|",
    )
    re_gbox = FemObjectReader.get_ff_regex("GBOX", "geono", "hz", "ty", "tb", "tt", "by", "sfy", "sfz")
    re_gbeamg = FemObjectReader.get_ff_regex(
        "GBEAMG",
        "geono",
        "comp",
        "area",
        "ix",
        "iy",
        "iz",
        "iyz",
        "wxmin",
        "wymin",
        "wzmin",
        "shary",
        "sharz",
        "shceny",
        "shcenz",
        "sy",
        "sz",
        "wy|",
        "wz|",
        "fabr|",
    )
    re_gpipe = FemObjectReader.get_ff_regex("GPIPE", "geono", "di", "dy", "t", "sfy", "sfz")
    re_lcsys = FemObjectReader.get_ff_regex("GUNIVEC", "transno", "unix", "uniy", "uniz")

    # Shell section
    re_thick = FemObjectReader.get_ff_regex("GELTH", "geono", "th")

    # Other
    re_bnbcd = FemObjectReader.get_ff_regex("BNBCD", "nodeno", "ndof", "content")
    re_belfix = FemObjectReader.get_ff_regex(
        "BELFIX",
        "fixno",
        "opt",
        "trano",
        "unused",
        "a1|",
        "a2|",
        "a3|",
        "a4|",
        "a5|",
        "a6|",
    )
    re_mgsprng = FemObjectReader.get_ff_regex("MGSPRNG", "matno", "ndof", "bulk")
    re_bnmass = FemObjectReader.get_ff_regex("BNMASS", "nodeno", "ndof", "m1", "m2", "m3", "m4", "m5", "m6")
    re_geccen = FemObjectReader.get_ff_regex("GECCEN", "eccno", "ex", "ey", "ez")
    re_bldep = FemObjectReader.get_ff_regex("BLDEP", "slave", "master", "nddof", "ndep", "bulk")
    re_setmembs = FemObjectReader.get_ff_regex("GSETMEMB", "nfield", "isref", "index", "istype", "isorig", "members")
    re_setnames = FemObjectReader.get_ff_regex("TDSETNAM", "nfield", "isref", "codnam", "codtxt", "set_name")

    # Materials
    re_matnames = FemObjectReader.get_ff_regex("TDMATER", "nfield", "geo_no", "codnam", "codtxt", "name")
    re_misosel = FemObjectReader.get_ff_regex(
        "MISOSEL", "matno", "young", "poiss", "rho", "damp", "alpha", "iyield", "yield"
    )
    re_morsmel = FemObjectReader.get_ff_regex(
        "MORSMEL",
        "matno",
        "q1",
        "q2",
        "q3",
        "rho",
        "d11",
        "d21",
        "d22",
        "d31",
        "d32",
        "d33",
        "ps1",
        "ps2",
        "damp1",
        "damp2",
        "alpha1",
        "alpha2",
    )

    el_map = {
        15: "B31",
        2: "B31",
        24: "S4R",
        25: "S3",
        40: "SPRING2",
        18: "SPRING1",
        11: "MASS",
    }

    """
    :param assembly: Assembly object
    :type assembly: ada.Assembly
    """

    def __init__(self, assembly, part_name="T1"):
        self.assembly = assembly
        self.part = Part(part_name)
        assembly.add_part(self.part)

    @classmethod
    def sesam_eltype_2_general(cls, eltyp):
        """
        Converts the numeric definition of elements in Sesam to a generalized element type form (ie. B31, S4, etc..)

        :param eltyp:
        :return: Generic element description
        """
        for ses, gen in cls.el_map.items():
            if cls.str_to_int(eltyp) == ses:
                return gen

        raise Exception("Currently unsupported eltype", eltyp)

    @classmethod
    def eltype_2_sesam(cls, eltyp):
        for ses, gen in cls.el_map.items():
            if eltyp == gen:
                return ses

        raise Exception("Currently unsupported eltype", eltyp)

    def read_sesam_fem(self, bulk_str):
        """
        Reads the content string of a Sesam input file and converts it to FEM objects

        :param bulk_str:
        """
        self.part.fem._nodes = self.get_nodes(bulk_str, self.part.fem)
        self.part.fem._elements = self.get_elements(bulk_str, self.part.fem)
        self.part.fem.elements.build_sets()
        self.part._materials = self.get_materials(bulk_str, self.part)
        self.part.fem._sets = self.part.fem.sets + self.get_sets(bulk_str, self.part.fem)
        self.part.fem._sections = self.get_sections(bulk_str, self.part.fem)
        # self.part.fem._masses = self.get_mass(bulk_str, self.part.fem)
        self.part.fem._constraints += self.get_constraints(bulk_str, self.part.fem)
        self.part.fem._springs = self.get_springs(bulk_str, self.part.fem)
        self.part.fem._bcs += self.get_bcs(bulk_str, self.part.fem)

        print(8 * "-" + f'Imported "{self.part.fem.instance_name}"')

    @classmethod
    def get_nodes(cls, bulk_str, parent):
        """
        Imports

        :param bulk_str:
        :param parent:
        :return: SortedNodes object
        :rtype: ada.core.containers.SortedNodes
        Format of input:

        GNODE     1.00000000E+00  1.00000000E+00  6.00000000E+00  1.23456000E+05
        GCOORD    1.00000000E+00  2.03000000E+02  7.05000000E+01  5.54650024E+02

        """
        from ada import Node
        from ada.core.containers import Nodes

        def get_node(m):
            d = m.groupdict()
            return Node(
                [float(d["x"]), float(d["y"]), float(d["z"])],
                int(float(d["id"])),
                parent=parent,
            )

        return Nodes(list(map(get_node, cls.re_gcoord_in.finditer(bulk_str))), parent=parent)

    @classmethod
    def get_elements(cls, bulk_str, parent):
        """
        Import elements from Sesam Bulk str


        :param bulk_str:
        :param parent:
        :type parent: ada.fem.FEM
        :return: FemElementsCollections
        :rtype: ada.fem.containers.FemElements
        """
        from ada.fem.containers import FemElements

        def grab_elements(match):
            d = match.groupdict()
            nodes = [
                parent.nodes.from_id(x)
                for x in filter(
                    lambda x: x != 0,
                    map(cls.str_to_int, d["nids"].replace("\n", "").split()),
                )
            ]
            eltyp = d["eltyp"]
            el_type = cls.sesam_eltype_2_general(eltyp)
            metadata = dict(eltyad=cls.str_to_int(d["eltyad"]), eltyp=eltyp)
            return Elem(
                cls.str_to_int(d["elno"]),
                nodes,
                el_type,
                None,
                parent=parent,
                metadata=metadata,
            )

        return FemElements(list(map(grab_elements, cls.re_gelmnt.finditer(bulk_str))), fem_obj=parent)

    @classmethod
    def get_materials(cls, bulk_str, parent):
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
        from ada.core.containers import Materials
        from ada.core.utils import roundoff

        def grab_name(m):
            d = m.groupdict()
            return cls.str_to_int(d["geo_no"]), d["name"]

        mat_names = {matid: mat_name for matid, mat_name in map(grab_name, cls.re_matnames.finditer(bulk_str))}

        def get_morsmel(m):
            """
            MORSMEL

            Anisotropy, Linear Elastic Structural Analysis, 2-D Membrane Elements and 2-D Thin Shell Elements

            :param m:
            :return:
            """

            d = m.groupdict()
            matno = cls.str_to_int(d["matno"])
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
            )

        def get_mat(match):
            d = match.groupdict()
            matno = cls.str_to_int(d["matno"])
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
            )

        return Materials(
            chain.from_iterable(
                [
                    map(get_mat, cls.re_misosel.finditer(bulk_str)),
                    map(get_morsmel, cls.re_morsmel.finditer(bulk_str)),
                ]
            ),
            parent=parent,
        )

    @classmethod
    def get_sets(cls, bulk_str, parent):
        from itertools import groupby
        from operator import itemgetter

        from ada.fem import FemSet
        from ada.fem.containers import FemSets

        def get_setmap(m):
            d = m.groupdict()
            set_type = "nset" if cls.str_to_int(d["istype"]) == 1 else "elset"
            if set_type == "nset":
                members = [parent.nodes.from_id(cls.str_to_int(x)) for x in d["members"].split()]
            else:
                members = [parent.elements.from_id(cls.str_to_int(x)) for x in d["members"].split()]
            return cls.str_to_int(d["isref"]), set_type, members

        set_map = dict()
        for setid_el_type, content in groupby(
            map(get_setmap, cls.re_setmembs.finditer(bulk_str)), key=itemgetter(0, 1)
        ):
            setid = setid_el_type[0]
            eltype = setid_el_type[1]
            set_map[setid] = [list(), eltype]
            for c in content:
                set_map[setid][0] += c[2]

        def get_femsets(m):
            nonlocal set_map
            d = m.groupdict()
            isref = cls.str_to_int(d["isref"])
            fem_set = FemSet(
                d["set_name"].strip(),
                set_map[isref][0],
                set_map[isref][1],
                parent=parent,
            )
            return fem_set

        return FemSets(list(map(get_femsets, cls.re_setnames.finditer(bulk_str))), fem_obj=parent)

    @staticmethod
    def get_hinges_from_elem(elem, members, hinges_global, lcsysd, xvec, zvec, yvec):
        """

        :param elem:
        :param members:
        :param hinges_global:
        # :param fem:
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
                )
            dofs_origin = [1, 2, 3, 4, 5, 6]
            d = [int(x) for x, i in zip(dofs_origin, (a1, a2, a3, a4, a5, a6)) if int(i) != 0]

            hinges.append((n, d, csys))
        return hinges

    @staticmethod
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

    @classmethod
    def get_sections(cls, bulk_str, fem):
        """

        General beam:
        GBEAMG    2.77000000E+02  0.00000000E+00  1.37400001E-01  6.93661906E-03
              1.07438751E-02  3.47881648E-03  0.00000000E+00  1.04544004E-02
              3.06967851E-02  1.39152659E-02  2.50000004E-02  2.08000001E-02
              0.00000000E+00  0.00000000E+00  6.04125019E-03  4.07929998E-03

        I-beam
        GIORH     1.00000000E+00  3.00000012E-01  7.10000005E-03  1.50000006E-01
              1.07000005E-02  1.50000006E-01  1.07000005E-02  1.00000000E+00
              1.00000000E+00

        GIORH (I-section description - if element type beam)
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

        :param bulk_str:
        :param fem: Parent object
        :type fem: ada.fem.FEM
        """
        from itertools import count

        import numpy as np

        from ada.core.containers import Sections
        from ada.core.utils import roundoff, unit_vector, vector_length
        from ada.fem.containers import FemSections

        # Get section names
        def get_section_names(m):
            d = m.groupdict()
            return cls.str_to_int(d["geono"]), d["set_name"].strip()

        sect_names = {sec_id: name for sec_id, name in map(get_section_names, cls.re_sectnames.finditer(bulk_str))}

        # Get local coordinate systems

        def get_lcsys(m):
            d = m.groupdict()
            return cls.str_to_int(d["transno"]), (
                roundoff(d["unix"]),
                roundoff(d["uniy"]),
                roundoff(d["uniz"]),
            )

        lcsysd = {transno: vec for transno, vec in map(get_lcsys, cls.re_lcsys.finditer(bulk_str))}

        # I-beam
        def get_IBeams(match):
            d = match.groupdict()
            sec_id = cls.str_to_int(d["geono"])
            return Section(
                name=sect_names[sec_id],
                sec_id=sec_id,
                sec_type="IG",
                h=roundoff(d["hz"]),
                t_w=roundoff(d["ty"]),
                w_top=roundoff(d["bt"]),
                w_btn=roundoff(d["bb"]),
                t_ftop=roundoff(d["tt"]),
                t_fbtn=roundoff(d["tb"]),
                genprops=GeneralProperties(sfy=float(d["sfy"]), sfz=float(d["sfz"])),
            )

        # Box-beam
        def get_BoxBeams(match):
            d = match.groupdict()
            sec_id = cls.str_to_int(d["geono"])
            return Section(
                name=sect_names[sec_id],
                sec_id=sec_id,
                sec_type="BG",
                h=roundoff(d["hz"]),
                w_top=roundoff(d["by"]),
                w_btn=roundoff(d["by"]),
                t_w=roundoff(d["ty"]),
                t_ftop=roundoff(d["tt"]),
                t_fbtn=roundoff(d["tb"]),
                genprops=GeneralProperties(sfy=float(d["sfy"]), sfz=float(d["sfz"])),
            )

        # General-beam
        def get_GenBeams(match):
            d = match.groupdict()
            sec_id = cls.str_to_int(d["geono"])
            gen_props = GeneralProperties(
                ax=roundoff(d["area"]),
                ix=roundoff(d["ix"]),
                iy=roundoff(d["iy"]),
                iz=roundoff(d["iz"]),
                iyz=roundoff(d["iyz"]),
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
                gen_props.edit(parent=sec)
            else:
                sec = Section(
                    name=f"GB{sec_id}",
                    sec_id=sec_id,
                    sec_type="GENBEAM",
                    genprops=gen_props,
                )
                gen_props.edit(parent=sec)
                fem.parent.sections.add(sec)

        # Tubular-beam
        def get_gpipe(match):
            d = match.groupdict()
            sec_id = cls.str_to_int(d["geono"])
            if sec_id not in sect_names:
                sec_name = f"TUB{sec_id}"
            else:
                sec_name = sect_names[sec_id]
            t = d["t"] if d["t"] is not None else (d["dy"] - d["di"]) / 2
            return Section(
                name=sec_name,
                sec_id=sec_id,
                sec_type="TUB",
                r=roundoff(float(d["dy"]) / 2),
                wt=roundoff(t),
                genprops=GeneralProperties(sfy=float(d["sfy"]), sfz=float(d["sfz"])),
            )

        def get_thicknesses(match):
            d = match.groupdict()
            sec_id = cls.str_to_int(d["geono"])
            t = d["th"]
            return sec_id, t

        def get_hinges(match):
            d = match.groupdict()
            fixno = cls.str_to_int(d["fixno"])
            opt = cls.str_to_int(d["opt"])
            trano = cls.str_to_int(d["trano"])
            a1 = cls.str_to_int(d["a1"])
            a2 = cls.str_to_int(d["a2"])
            a3 = cls.str_to_int(d["a3"])
            a4 = cls.str_to_int(d["a4"])
            a5 = cls.str_to_int(d["a5"])
            try:
                a6 = cls.str_to_int(d["a6"])
            except BaseException as e:
                logging.debug(e)
                a6 = 0
                pass
            return fixno, (opt, trano, a1, a2, a3, a4, a5, a6)

        def get_eccentricities(match):
            d = match.groupdict()
            eccno = cls.str_to_int(d["eccno"])
            ex = float(d["ex"])
            ey = float(d["ey"])
            ez = float(d["ez"])
            return eccno, (ex, ey, ez)

        hinges_global = {fixno: values for fixno, values in map(get_hinges, cls.re_belfix.finditer(bulk_str))}
        thicknesses = {geono: t for geono, t in map(get_thicknesses, cls.re_thick.finditer(bulk_str))}
        eccentricities = {eccno: values for eccno, values in map(get_eccentricities, cls.re_geccen.finditer(bulk_str))}

        list_of_sections = list(
            chain.from_iterable(
                [
                    map(get_IBeams, cls.re_giorh.finditer(bulk_str)),
                    map(get_BoxBeams, cls.re_gbox.finditer(bulk_str)),
                    map(get_gpipe, cls.re_gpipe.finditer(bulk_str)),
                ]
            )
        )
        fem.parent._sections = Sections(list_of_sections)
        list(map(get_GenBeams, cls.re_gbeamg.finditer(bulk_str)))

        importedgeom_counter = count(1)
        total_geo = count(1)

        def get_femsecs(match):
            d = match.groupdict()
            geono = cls.str_to_int(d["geono"])
            next(total_geo)
            transno = cls.str_to_int(d["transno"])
            elno = cls.str_to_int(d["elno"])
            elem = fem.elements.from_id(elno)

            matno = cls.str_to_int(d["matno"])

            # Go no further if element has no fem section
            if elem.type in elem.springs + elem.masses:
                next(importedgeom_counter)
                elem.metadata["matno"] = matno
                return None

            mat = fem.parent.materials.get_by_id(matno)
            if elem.type in Elem.beam:
                next(importedgeom_counter)
                sec = fem.parent.sections.get_by_id(geono)
                n1, n2 = elem.nodes
                v = n2.p - n1.p
                if vector_length(v) == 0.0:
                    xvec = [1, 0, 0]
                else:
                    xvec = unit_vector(v)
                zvec = lcsysd[transno]
                crossed = np.cross(xvec, zvec)
                ma = max(abs(crossed))
                yvec = tuple([roundoff(x / ma, 3) for x in crossed])

                fix_data = cls.str_to_int(d["fixno"])
                ecc_data = cls.str_to_int(d["eccno"])

                members = None
                if d["members"] is not None:
                    members = [cls.str_to_int(x) for x in d["members"].replace("\n", " ").split()]

                hinges = None
                if fix_data == -1:
                    hinges = cls.get_hinges_from_elem(elem, members, hinges_global, lcsysd, xvec, zvec, yvec)

                offset = None
                if ecc_data == -1:
                    offset = cls.get_ecc_from_elem(elem, members, eccentricities, fix_data)

                fem_set = FemSet(sec.name, [elem], "elset", metadata=dict(internal=True), parent=fem)
                fem.sets.add(fem_set, append_suffix_on_exist=True)
                fem_sec = FemSection(
                    name=sec.name,
                    sec_type="beam",
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

            elif elem.type in Elem.shell:
                next(importedgeom_counter)
                sec_name = f"sh{elno}"
                fem_set = FemSet(sec_name, [elem], "elset", parent=fem, metadata=dict(internal=True))
                fem.sets.add(fem_set)
                fem_sec = FemSection(
                    name=sec_name,
                    sec_type="shell",
                    thickness=roundoff(thicknesses[geono]),
                    elset=fem_set,
                    material=mat,
                    parent=fem,
                )
                return fem_sec
            else:
                raise ValueError("Section not added to conversion")

        sections = list(filter(None, map(get_femsecs, cls.re_gelref1.finditer(bulk_str))))
        print(f"Successfully imported {next(importedgeom_counter) - 1} FEM sections out of {next(total_geo) - 1}")
        return FemSections(sections, fem_obj=fem)

    @classmethod
    def get_mass(cls, bulk_str, fem):
        """

        :param bulk_str:
        :param fem:
        :type fem: ada.fem.FEM
        :return:
        """
        from ada.core.utils import roundoff

        def checkEqual2(iterator):
            return len(set(iterator)) <= 1

        def grab_mass(match):
            d = match.groupdict()

            nodeno = cls.str_to_int(d["nodeno"])
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

        return {m.name: m for m in map(grab_mass, cls.re_bnmass.finditer(bulk_str))}

    @classmethod
    def get_constraints(cls, bulk_str, fem):
        """

        :param bulk_str:
        :param fem:
        :type fem: ada.fem.FEM
        :return:
        """

        def grab_constraint(master, data):
            m = cls.str_to_int(master)
            m_set = FemSet(f"co{m}_m", [fem.nodes.from_id(m)], "nset")
            slaves = []
            for d in data:
                s = cls.str_to_int(d["slave"])
                slaves.append(fem.nodes.from_id(s))
            s_set = FemSet(f"co{m}_m", slaves, "nset")
            fem.add_set(m_set)
            fem.add_set(s_set)
            return Constraint(f"co{m}", "coupling", m_set, s_set, parent=fem)

        con_map = [m.groupdict() for m in cls.re_bldep.finditer(bulk_str)]
        con_map.sort(key=lambda x: x["master"])
        from itertools import groupby

        return [grab_constraint(m, d) for m, d in groupby(con_map, key=lambda x: x["master"])]

    @classmethod
    def get_springs(cls, bulk_str, fem):
        from itertools import groupby
        from operator import attrgetter

        import numpy as np

        gr_spr_elements = None
        for eltype, elements in groupby(fem.elements, key=attrgetter("type")):
            if eltype == "SPRING1":
                gr_spr_elements = {el.metadata["matno"]: el for el in elements}

        def grab_grspring(m):
            nonlocal gr_spr_elements
            d = m.groupdict()
            matno = cls.str_to_int(d["matno"])
            ndof = cls.str_to_int(d["ndof"])
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
                    new_s.append([0 for i in range(0, l)] + row)
                else:
                    new_s.append(row)
            X = np.array(new_s)
            X = X + X.T - np.diag(np.diag(X))
            return Spring(spr_name, matno, "SPRING1", n1=n1, stiff=X, parent=fem)

        return {c.name: c for c in map(grab_grspring, cls.re_mgsprng.finditer(bulk_str))}

    @classmethod
    def get_bcs(cls, bulk_str, fem):
        """

        :param bulk_str:
        :param fem:
        :type fem: ada.fem.FEM
        :return:
        """
        from ada import Node
        from ada.fem import Bc

        def grab_bc(match):
            d = match.groupdict()
            node = fem.nodes.from_id(cls.str_to_int(d["nodeno"]))
            assert isinstance(node, Node)

            fem_set = FemSet(f"bc{node.id}_set", [node], "nset")
            fem.sets.add(fem_set)
            dofs = []
            for i, c in enumerate(d["content"].replace("\n", "").split()):
                bc_sestype = cls.str_to_int(c.strip())
                if bc_sestype in [0, 4]:
                    continue
                dofs.append(i + 1)
            bc = Bc(f"bc{node.id}", fem_set, dofs, parent=fem)
            node.bc = bc

            return bc

        return list(map(grab_bc, cls.re_bnbcd.finditer(bulk_str)))


class SesamWriter(Assembly, FemWriter):
    """
    `Sesam <https://usfos.no/>`_ is a nonlinear static and dynamic analysis of space frame structures.

    """

    analysis_path = None

    def __init__(self, origin):
        super(SesamWriter, self).__init__(origin.name)
        self.__dict__.update(origin.__dict__)
        parts = list(filter(lambda x: len(x.fem.nodes) > 0, self.get_all_subparts()))
        if len(parts) != 1:
            raise ValueError("Sesam writer currently only works for a single part")
        part = parts[0]
        from operator import attrgetter

        self._gnodes = sorted(part.fem.nodes, key=attrgetter("id"))
        self._gloads = part.fem.steps[0].loads if len(part.fem.steps) > 0 else []
        self._gelements = part.fem.elements
        self._gsections = part.fem.sections
        self._gmaterials = Materials()
        for fsec in self._gsections:
            self._gmaterials.add(fsec.material)
        self._gelsets = part.fem.elsets
        self._gnsets = part.fem.nsets
        self._gmass = part.fem.masses
        self._gbcs = part.fem.bcs
        self._gnonstru = []
        self._geccen = []
        self._thick_map = None

    def write(
        self,
        name,
        scratch_dir=None,
        description=None,
        execute=False,
        run_ext=False,
        cpus=2,
        gpus=None,
        overwrite=False,
    ):
        self._write_dir(scratch_dir, name, overwrite)
        import datetime

        now = datetime.datetime.now()
        date_str = now.strftime("%d-%b-%Y")
        clock_str = now.strftime("%H:%M:%S")
        user = os.getlogin()

        units = "UNITS     5.00000000E+00  1.00000000E+00  1.00000000E+00  1.00000000E+00\n          1.00000000E+00\n"

        with open((self.analysis_path / "T100").with_suffix(".FEM"), "w") as d:
            d.write(
                f"""IDENT     1.00000000E+00  1.00000000E+02  3.00000000E+00  0.00000000E+00
DATE      1.00000000E+00  0.00000000E+00  4.00000000E+00  7.20000000E+01
DATE:     {date_str}         TIME:          {clock_str}
PROGRAM:  ADA python          VERSION:       Not Applicable
COMPUTER: X86 Windows         INSTALLATION:
USER:     {user}            ACCOUNT:     \n"""
            )
            d.write(units)
            d.write(self._materials_str)
            d.write(self._sections_str)
            d.write(self._nodes_str)
            d.write(self._bc_str)
            d.write(self._elem_str)
            d.write(self._loads_str)
            d.write("IEND                0.00            0.00            0.00            0.00")

        print(f'Created an Sesam input deck at "{self.analysis_path}"')

    @property
    def _materials_str(self):
        """

        'TDMATER', 'nfield', 'geo_no', 'codnam', 'codtxt', 'name'
        'MISOSEL', 'matno', 'young', 'poiss', 'rho', 'damp', 'alpha', 'iyield', 'yield'

        :return:
        """
        materials = self._gmaterials
        out_str = "".join(
            [self.write_ff("TDMATER", [(4, mat.id, 100 + len(mat.name), 0), (mat.name,)]) for mat in materials]
        )

        out_str += "".join(
            [
                self.write_ff(
                    "MISOSEL",
                    [
                        (mat.id, mat.model.E, mat.model.v, mat.model.rho),
                        (mat.model.zeta, mat.model.alpha, 1, mat.model.sig_y),
                    ],
                )
                for mat in materials
            ]
        )
        return out_str

    @property
    def _sections_str(self):
        """

        'TDSECT', 'nfield', 'geono', 'codnam', 'codtxt', 'set_name'
        'GIORH ', 'geono', 'hz', 'ty', 'bt', 'tt', 'bb', 'tb', 'sfy', 'sfz', 'NLOBYT|', 'NLOBYB|', 'NLOBZ|'

        :return:
        """
        from ada.core.utils import Counter
        from ada.sections import SectionCat

        sec_str = ""
        sec_ids = []
        names_str = ""
        concept_str = ""
        thick_map = dict()
        c = Counter(0)
        g = Counter(0)
        ircon = Counter(0)
        shid = Counter(0)
        bmid = Counter(0)
        for sec in self._gsections:
            assert isinstance(sec, FemSection)
            if sec.type == "beam":
                section = sec.section
                assert isinstance(section, Section)
                if sec.section not in sec_ids:
                    secid = next(bmid)
                    section.metadata["numid"] = secid
                    names_str += self.write_ff(
                        "TDSECT",
                        [
                            (4, secid, 100 + len(sec.section.name), 0),
                            (sec.section.name,),
                        ],
                    )
                    sec_ids.append(sec.section)
                    if "beam" in sec.metadata.keys():
                        # Give concept relationship based on inputted values
                        beam = sec.metadata["beam"]
                        numel = sec.metadata["numel"]
                        sec.metadata["ircon"] = next(ircon)
                        concept_str += self.write_ff(
                            "TDSCONC",
                            [(4, sec.metadata["ircon"], 100 + len(beam.guid), 0), (beam.guid,)],
                        )
                        concept_str += self.write_ff("SCONCEPT", [(8, next(c), 7, 0), (0, 1, 0, 2)])
                        sconc_ref = next(c)
                        concept_str += self.write_ff("SCONCEPT", [(5, sconc_ref, 2, 4), (1,)])
                        elids = []
                        i = 0
                        elid_bulk = [numel]
                        for el in sec.elset.members:
                            if i == 3:
                                elids.append(tuple(elid_bulk))
                                elid_bulk = []
                                i = -1
                            elid_bulk.append(el.id)
                            i += 1
                        if len(elid_bulk) != 0:
                            elids.append(tuple(elid_bulk))
                            elid_bulk = []

                        mesh_args = [(5 + numel, sconc_ref, 1, 2)] + elids
                        concept_str += self.write_ff("SCONMESH", mesh_args)
                        concept_str += self.write_ff("GUNIVEC", [(next(g), *beam.up)])

                    section.properties.calculate()
                    p = section.properties
                    sec_str += self.write_ff(
                        "GBEAMG",
                        [
                            (secid, 0, p.Ax, p.Ix),
                            (p.Iy, p.Iz, p.Iyz, p.Wxmin),
                            (p.Wymin, p.Wzmin, p.Shary, p.Sharz),
                            (p.Scheny, p.Schenz, p.Sy, p.Sz),
                        ],
                    )

                    if SectionCat.is_i_profile(section.type):
                        sec_str += self.write_ff(
                            "GIORH",
                            [
                                (secid, section.h, section.t_w, section.w_top),
                                (section.t_ftop, section.w_top, section.t_ftop, p.Sfy),
                                (p.Sfz,),
                            ],
                        )
                    elif SectionCat.is_hp_profile(section.type):
                        sec_str += self.write_ff(
                            "GLSEC",
                            [
                                (secid, section.h, section.t_w, section.w_btn),
                                (section.t_fbtn, p.Sfy, p.Sfz, 1),
                            ],
                        )
                    elif SectionCat.is_box_profile(section.type):
                        sec_str += self.write_ff(
                            "GBOX",
                            [
                                (secid, section.h, section.t_w, section.t_fbtn),
                                (section.t_ftop, section.w_btn, p.Sfy, p.Sfz),
                            ],
                        )
                    elif SectionCat.is_circular_profile(section.type):
                        sec_str += self.write_ff(
                            "GPIPE",
                            [(secid, section.r - section.wt, section.r, section.wt), (p.Sfy, p.Sfz)],
                        )
                    else:
                        logging.error(f'Unable to convert "{section}". This will be exported as general section only')

            elif sec.type == "shell":
                if sec.thickness not in thick_map.keys():
                    sh_id = next(shid)
                    thick_map[sec.thickness] = sh_id
                else:
                    sh_id = thick_map[sec.thickness]
                sec_str += self.write_ff("GELTH", [(sh_id, sec.thickness, 5)])
            else:
                raise ValueError(f"Unknown type {sec.type}")
        self._thick_map = thick_map
        return names_str + sec_str + concept_str

    @property
    def _elem_str(self):
        """

        'GELREF1',  ('elno', 'matno', 'addno', 'intno'), ('mintno', 'strano', 'streno', 'strepono'), ('geono', 'fixno',
                    'eccno', 'transno'), 'members|'

        'GELMNT1', 'elnox', 'elno', 'eltyp', 'eltyad', 'nids'

        :return:
        """
        elements = self._gelements

        out_str = "".join(
            [
                self.write_ff(
                    "GELMNT1",
                    [
                        (el.id, el.id, SesamReader.eltype_2_sesam(el.type), 0),
                        ([n.id for n in el.nodes]),
                    ],
                )
                for el in elements
            ]
        )
        for el in elements:
            fem_sec = el.fem_sec
            assert isinstance(fem_sec, FemSection)
            if fem_sec.type == "beam":
                sec_id = fem_sec.section.metadata["numid"]
            elif fem_sec.type == "shell":
                sec_id = self._thick_map[fem_sec.thickness]
            else:
                raise ValueError(f'Unsupported elem type "{fem_sec.type}"')
            out_str += self.write_ff(
                "GELREF1",
                [
                    (el.id, el.fem_sec.material.id, 0, 0),
                    (0, 0, 0, 0),
                    (sec_id, 0, 0, 1),
                ],
            )
        return out_str

    @property
    def _nodes_str(self):
        """
        GNODE: GNODE NODEX NODENO NDOF ODOF NODEX

        """

        nodes = self._gnodes

        nids = []
        for n in nodes:
            if n.id not in nids:
                nids.append(n.id)
            else:
                raise Exception('Doubly defined node id "{}". TODO: Make necessary code updates'.format(n[0]))
        if len(nodes) == 0:
            return "** No Nodes"
        else:

            out_str = "".join([self.write_ff("GNODE", [(no.id, no.id, 6, 123456)]) for no in nodes])
            out_str += "".join([self.write_ff("GCOORD", [(no.id, no[0], no[1], no[2])]) for no in nodes])
            return out_str

    @property
    def _bc_str(self):
        out_str = ""
        for bc in self._gbcs:
            for m in bc.fem_set.members:
                dofs = [1 if i in bc.dofs else 0 for i in range(1, 7)]
                data = [tuple([m.id, 6] + dofs[:2]), tuple(dofs[2:])]
                out_str += self.write_ff("BNBCD", data)
        return out_str

    @property
    def _loads_str(self):
        out_str = ""
        for i, l in enumerate(self._gloads):
            assert isinstance(l, Load)
            lid = i + 1
            out_str += self.write_ff("TDLOAD", [(4, lid, 100 + len(l.name), 0), (l.name,)])
            if l.type in ["acc", "grav"]:
                out_str += self.write_ff(
                    "BGRAV",
                    [(lid, 0, 0, 0), tuple([x * l.magnitude for x in l.acc_vector])],
                )
        return out_str

    @staticmethod
    def write_ff(flag, data):
        """
        flag = NCOD
        data = [(int, float, int, float), (float, int)]

        ->> NCOD    INT     FLOAT       INT     FLOAT
                    FLOAT   INT

        :param flag:
        :param data:
        :return:
        """

        def write_data(d):
            if type(d) in (np.float64, float, int, np.uint64, np.int32) and d >= 0:
                return f"  {d:<14.8E}"
            elif type(d) in (np.float64, float, int, np.uint64, np.int32) and d < 0:
                return f" {d:<15.8E}"
            elif type(d) is str:
                return d
            else:
                raise ValueError(f"Unknown input {type(d)} {d}")

        out_str = f"{flag:<8}"
        for row in data:
            v = [write_data(x) for x in row]
            if row == data[-1]:
                out_str += "".join(v) + "\n"
            else:
                out_str += "".join(v) + "\n" + 8 * " "
        return out_str
