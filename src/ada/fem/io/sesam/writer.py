import logging

import numpy as np

from ada.core.containers import Materials
from ada.core.utils import get_current_user
from ada.fem import FemSection, Load
from ada.fem.io.utils import _folder_prep


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


class SesamWriter:
    """
    `Sesam <https://www.dnv.com/software/products/sesam-products.html>`_ is a brand of services from DNV covering
    FEM and Hydrodynamics for maritime applications.

    """

    def __init__(self, assembly):
        parts = list(filter(lambda x: len(x.fem.nodes) > 0, assembly.get_all_subparts()))
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
        analysis_dir = _folder_prep(scratch_dir, name, overwrite)
        import datetime

        now = datetime.datetime.now()
        date_str = now.strftime("%d-%b-%Y")
        clock_str = now.strftime("%H:%M:%S")
        user = get_current_user()

        units = "UNITS     5.00000000E+00  1.00000000E+00  1.00000000E+00  1.00000000E+00\n          1.00000000E+00\n"

        with open((analysis_dir / "T100").with_suffix(".FEM"), "w") as d:
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
            d.write(self._mass_str)
            d.write(self._bc_str)
            d.write(self._hinges_str)
            d.write(self._univec_str)
            d.write(self._elem_str)
            d.write(self._loads_str)
            d.write("IEND                0.00            0.00            0.00            0.00")

        print(f'Created an Sesam input deck at "{analysis_dir}"')

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
        c = Counter(1)
        g = Counter(1)
        ircon = Counter(1)
        shid = Counter(1)
        bmid = Counter(1)
        for fem_sec in self._gsections:
            assert isinstance(fem_sec, FemSection)
            if fem_sec.type == "beam":
                section = fem_sec.section
                if section not in sec_ids:
                    secid = next(bmid)
                    section.metadata["numid"] = secid
                    names_str += self.write_ff(
                        "TDSECT",
                        [
                            (4, secid, 100 + len(fem_sec.section.name), 0),
                            (fem_sec.section.name,),
                        ],
                    )
                    sec_ids.append(fem_sec.section)
                    if "beam" in fem_sec.metadata.keys():
                        # Give concept relationship based on inputted values
                        beam = fem_sec.metadata["beam"]
                        numel = fem_sec.metadata["numel"]
                        fem_sec.metadata["ircon"] = next(ircon)
                        concept_str += self.write_ff(
                            "TDSCONC",
                            [(4, fem_sec.metadata["ircon"], 100 + len(beam.guid), 0), (beam.guid,)],
                        )
                        concept_str += self.write_ff("SCONCEPT", [(8, next(c), 7, 0), (0, 1, 0, 2)])
                        sconc_ref = next(c)
                        concept_str += self.write_ff("SCONCEPT", [(5, sconc_ref, 2, 4), (1,)])
                        elids = []
                        i = 0
                        elid_bulk = [numel]
                        for el in fem_sec.elset.members:
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
                                (section.t_ftop, section.w_btn, section.t_fbtn, p.Sfy),
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
                    elif SectionCat.is_flatbar(section.type):
                        sec_str += self.write_ff(
                            "GBARM", [(secid, section.h, section.w_top, section.w_btn), (p.Sfy, p.Sfz)]
                        )
                    else:
                        logging.error(f'Unable to convert "{section}". This will be exported as general section only')

            elif fem_sec.type == "shell":
                if fem_sec.thickness not in thick_map.keys():
                    sh_id = next(shid)
                    thick_map[fem_sec.thickness] = sh_id
                else:
                    sh_id = thick_map[fem_sec.thickness]
                sec_str += self.write_ff("GELTH", [(sh_id, fem_sec.thickness, 5)])
            else:
                raise ValueError(f"Unknown type {fem_sec.type}")
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
        from .reader import SesamReader

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

        def write_elem(el):
            """

            :param el:
            :type el: ada.fem.Elem
            :return: input str for Elem
            """
            fem_sec = el.fem_sec
            assert isinstance(fem_sec, FemSection)
            if fem_sec.type == "beam":
                sec_id = fem_sec.section.metadata["numid"]
            elif fem_sec.type == "shell":
                sec_id = self._thick_map[fem_sec.thickness]
            else:
                raise ValueError(f'Unsupported elem type "{fem_sec.type}"')

            fixno = el.metadata.get("fixno", None)
            transno = el.metadata.get("transno")
            if fixno is None:
                last_tuples = [(sec_id, 0, 0, transno)]
            else:
                h1_fix, h2_fix = fixno
                last_tuples = [(sec_id, -1, 0, transno), (h1_fix, h2_fix)]

            return self.write_ff(
                "GELREF1",
                [
                    (el.id, el.fem_sec.material.id, 0, 0),
                    (0, 0, 0, 0),
                ]
                + last_tuples,
            )

        for el in elements:
            out_str += write_elem(el)

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
    def _mass_str(self):
        out_str = ""

        for mass in self._gmass.values():
            for m in mass.fem_set.members:
                if type(mass.mass) in (int, float, np.float64):
                    masses = [mass.mass for _ in range(0, 3)] + [0, 0, 0]
                else:
                    raise NotImplementedError()
                data = (tuple([m.id, 6] + masses[:2]), tuple(masses[2:]))
                out_str += self.write_ff("BNMASS", data)
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
    def _hinges_str(self):
        from ada.core.utils import Counter

        out_str = ""
        h = Counter(1)

        def write_hinge(hinge):
            dofs = [0 if i in hinge else 1 for i in range(1, 7)]
            fix_id = next(h)
            data = [tuple([fix_id, 3, 0, 0]), tuple(dofs[:4]), tuple(dofs[4:])]
            return fix_id, self.write_ff("BELFIX", data)

        for el in self._gelements:
            h1, h2 = el.metadata.get("h1", None), el.metadata.get("h2", None)
            if h2 is None and h1 is None:
                continue
            h1_fix, h2_fix = 0, 0
            if h1 is not None:
                h1_fix, res_str = write_hinge(h1)
                out_str += res_str
            if h2 is not None:
                h2_fix, res_str = write_hinge(h2)
                out_str += res_str
            el.metadata["fixno"] = h1_fix, h2_fix

        return out_str

    @property
    def _univec_str(self):
        from ada.core.utils import Counter

        out_str = ""
        unit_vector = Counter(1)

        def write_local_z(vec):
            transno = next(unit_vector)
            data = [tuple([transno, *vec])]
            return transno, self.write_ff("GUNIVEC", data)

        for el in self._gelements:
            local_z = el.fem_sec.local_z
            transno, res_str = write_local_z(local_z)
            out_str += res_str
            el.metadata["transno"] = transno

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
