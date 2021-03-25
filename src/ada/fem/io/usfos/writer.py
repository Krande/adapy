import os
from operator import attrgetter

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
    exit_on_complete=True,
):
    """

    :param assembly:
    :param name:
    :param scratch_dir:
    :param metadata:
    :param execute:
    :param run_ext:
    :param cpus:
    :param gpus:
    :param overwrite:
    :param exit_on_complete
    """
    if metadata is None:
        metadata = dict()
    if "control_file" not in metadata.keys():
        metadata["control_file"] = None

    a = UsfosWriter(assembly)
    a.write(name, scratch_dir, metadata, overwrite=overwrite)


class UsfosWriter:
    """
    `Usfos <https://usfos.no/>`_ is a nonlinear static and dynamic analysis of space frame structures.

    """

    def __init__(self, assembly):
        parts = list(filter(lambda x: len(x.fem.nodes) > 0, assembly.get_all_subparts()))
        if len(parts) != 1:
            raise ValueError("Usfos writer currently only works for a single part")
        part = parts[0]
        self.part = part
        self._gmaterials = assembly.materials
        self._gnodes = part.fem.nodes
        self._gelements = part.fem.elements
        self._gsections = part.fem.sections
        self._gelsets = part.fem.elsets
        self._gnsets = part.fem.nsets
        self._gmass = part.fem.masses
        self._gnonstru = []
        self._geccen = []

    def write(
        self,
        name,
        scratch_dir=None,
        metadata=None,
        execute=False,
        run_ext=False,
        overwrite=False,
    ):
        analysis_dir = _folder_prep(scratch_dir, name, overwrite)

        head = """ HEAD\n\n\n"""

        # Need to create these before beams and shell in order to
        # sec_str = self.sections_str
        # mat_str = self.materials_str

        with open(os.path.join(analysis_dir, r"ufo_bulk.fem"), "w") as d:
            d.write(head)
            d.write(self.nodal_str + "\n")
            d.write(self.beam_str + "\n")
            d.write(shell_str(self.part) + "\n")
            d.write(self.eccent_str + "\n")
            d.write(self.sections_str + "\n")
            d.write(materials_str(self._gmaterials) + "\n")
            d.write(self.mass_str + "\n")
            d.write(self.create_usfos_set_str + "\n")

        if metadata["control_file"] is not None:
            with open(os.path.join(analysis_dir, r"usfos.fem"), "w") as d:
                d.write(metadata["control_file"] + "\n")
                # d.write(self.nonstru_str + '\n')

        print(f'Created an Usfos input deck at "{analysis_dir}"')

    @property
    def gnodes(self):
        """

        :return:
        :rtype: ada.core.containers.Nodes
        """
        return self._gnodes

    @property
    def gelements(self):
        """

        :return:
        :rtype: ada.fem.containers.FemElements
        """
        return self._gelements

    @property
    def nodal_str(self):
        """
        :return: str
        """
        node_str = "'            Node ID            X              Y              Z    Boundary code\n"
        f = " NODE {nid:>15} {x:>13.3f} {y:>13.3f} {z:>13.3f}{bc}"

        def write_bc(bc):
            """

            :param bc:
            :type bc: ada.fem.Bc
            """
            bcs_str = ""
            for dof in range(1, 7):
                if dof in bc.dofs:
                    bcs_str += " 1"
                else:
                    bcs_str += " 0"

            return bcs_str

        def write_node(no):
            """

            :param no:
            :type no: ada.Node
            """
            bc_str = "" if no.bc is None else write_bc(no.bc)
            return f.format(nid=no.id, x=no[0], y=no[1], z=no[2], bc=bc_str)

        return (
            node_str + "\n".join(list(map(write_node, sorted(self._gnodes, key=attrgetter("id"))))).rstrip()
            if len(self._gnodes) > 0
            else "** No Nodes"
        )

    @property
    def beam_str(self):
        """

        # USFOS Strings

        # Beam String
        '            Elem ID     np1      np2   material   geom    lcoor    ecc1    ecc2
        BEAM            1127     1343     1344        1        1       1

        # Unit Vector String
        '            Loc-Coo           dx             dy             dz
        UNITVEC    60000001        0.00000        0.00000        1.00000

        """
        from ada.core.utils import Counter

        locvecs = []
        eccen_counter = Counter(1)
        loc_str = "'\n'            Loc-Coo           dx             dy             dz\n"
        bm_str = "'\n'            Elem ID     np1      np2   material   geom    lcoor    ecc1    ecc2\n"

        def write_elem(el):
            """

            :param el:
            :type el: ada.fem.Elem
            """
            nonlocal locvecs
            n1 = el.nodes[0]
            n2 = el.nodes[1]
            fem_sec = el.fem_sec
            mat = fem_sec.material
            sec = fem_sec.section
            xvec = fem_sec.local_z
            xvec_str = f"{xvec[0]:>13.5f}{xvec[1]:>15.5f}{xvec[2]:>15.5f}"

            mat_id = self._gmaterials.index(mat) + 1

            if xvec_str in locvecs:
                locid = locvecs.index(xvec_str)
            else:
                locvecs.append(xvec_str)
                locid = locvecs.index(xvec_str)

            if fem_sec.offset is not None:
                ecc1_str = " 0"
                ecc2_str = " 0"
                for n, e in fem_sec.offset:
                    if n == n1:
                        ecc1 = next(eccen_counter)
                        self._geccen.append((ecc1, e))
                        ecc1_str = f" {ecc1}"
                    if n == n2:
                        ecc2 = next(eccen_counter)
                        self._geccen.append((ecc2, e))
                        ecc2_str = f" {ecc2}"
            else:
                ecc1_str = ""
                ecc2_str = ""
            return f" BEAM{el.id:>15}{n1.id:>8}{n2.id:>9}{mat_id:>11}{sec.id:>7}{locid + 1:>9}{ecc1_str}{ecc2_str}"

        bm_str += "\n".join(list(map(write_elem, self._gelements.beams)))

        for i, loc in enumerate(locvecs):
            loc_str += " UNITVEC{:>13}{:<10}\n".format(i + 1, loc)

        return bm_str + "\n" + loc_str

    @property
    def sections_str(self):
        """
        This method takes in a section object and returns the sesam_lib string for use in js-files.

        """
        from ada import Section
        from ada.sections import SectionCat

        space = 20 * " "

        box = " BOX{id:>16}{h:>6.3f}{t_w:>10.3f}{t_ftop:>8.3f}{t_fbtn:>8.3f}{w_top:>8.3f}\n"
        tub = " PIPE{id:>14}{d:>14.3f}{wt:>14.3f}\n"
        ipe = " IHPROFIL{id:>13}{h:>13.3f}{t_w:>13.3f}{w_top:>13.3f}{t_ftop:>13.3f}{w_btn:>13.3f}{t_fbtn:>13.3f}\n"
        gen = (
            " GENBEAM{id:>11}{area:>11.3E}{it:>11.3E}{iy:>11.3E}{iz:>11.3E}\n{wpx:>11.3E}{wpy:>11.3E}"
            "{wpz:>11.3E}{shy:>11.3E}\n{shz:>11.3E}\n"
        )
        box_str = f"' Box Profiles\n'{space}Geom ID     H     T-sid   T-bot   T-top   Width   Sh_y Sh_z\n"
        tub_str = f"' Tubulars\n'{space}Geom ID       Do         Thick   (Shear_y   Shear_z      Diam2 )\n"
        circ_str = f"' Circulars\n'{space}Geom ID       Do         Thick   (Shear_y   Shear_z      Diam2 )\n"
        ip_str = f"' I-Girders\n'{space}Geom ID     H     T-web    W-top   T-top    W-bot   T-bot Sh_y Sh_z\n"
        tp_str = f"' T-profiles\n'{space}Geom ID     H     T-web    W-top   T-top    W-bot   T-bot Sh_y Sh_z\n"
        ang_str = f"' HP profiles\n'{space}Geom ID     H     T-web    W-top   T-top    W-bot   T-bot Sh_y Sh_z\n"
        cha_str = f"' Channels\n'{space}Geom ID     H     T-web    W-top   T-top    W-bot   T-bot Sh_y Sh_z\n"
        gens_str = f"' General Beams\n'{space}Geom ID     \n"

        for s in sorted(self.part.sections, key=attrgetter("id")):
            assert isinstance(s, Section)
            if s.type in SectionCat.box:
                # BOX      100000001    0.500   0.016   0.016   0.016    0.500
                box_str += box.format(
                    id=s.id,
                    h=s.h,
                    t_w=s.t_w,
                    t_ftop=s.t_ftop,
                    t_fbtn=s.t_fbtn,
                    w_top=s.w_top,
                )
            elif s.type in SectionCat.tubular:
                # PIPE      60000001       1.010       0.045
                tub_str += tub.format(id=s.id, d=s.r * 2, wt=s.wt)
            elif s.type in SectionCat.circular:
                # PIPE      60000001       1.010       0.045
                circ_str += tub.format(id=s.id, d=s.r * 2, wt=s.r * 0.99)
            elif s.type in SectionCat.iprofiles + SectionCat.igirders:
                # IHPROFIL     11011    0.590   0.013    0.300   0.025    0.300   0.025
                ip_str += ipe.format(
                    id=s.id,
                    h=s.h,
                    t_w=s.t_w,
                    w_top=s.w_top,
                    t_ftop=s.t_ftop,
                    w_btn=s.w_btn,
                    t_fbtn=s.t_fbtn,
                )
            elif s.type in SectionCat.tprofiles:
                print(f'T-Profiles currently not considered. Relevant for bm id "{s.id}". Will use IPE for now')
                # IHPROFIL     11011    0.590   0.013    0.300   0.025    0.300   0.025
                tp_str += ipe.format(
                    id=s.id,
                    h=s.h,
                    t_w=s.t_w,
                    w_top=s.w_top,
                    t_ftop=s.t_ftop,
                    w_btn=s.w_btn,
                    t_fbtn=s.t_fbtn,
                )

            elif s.type in SectionCat.angular:
                print(f'Angular-Profiles are not supported by USFOS. Bm "{s.id}" will use GENBEAM')
                gp = s.properties
                gp.calculate()
                gens_str += gen.format(
                    id=s.id,
                    area=gp.Ax,
                    it=gp.Ix,
                    iy=gp.Iy,
                    iz=gp.Iz,
                    wpx=gp.Wxmin,
                    wpy=gp.Wymin,
                    wpz=gp.Wzmin,
                    shy=gp.Shary,
                    shz=gp.Sharz,
                )
                # raise ValueError('Angular profiles currently not considered. Relevant for bm id "{}"'.format(s.id))

            elif s.type in SectionCat.channels:
                print(f'Channel-Profiles are not supported by USFOS. Bm "{s.id}" will use GENBEAM')
                gp = s.properties
                gp.calculate()
                gens_str += gen.format(
                    id=s.id,
                    area=gp.Ax,
                    it=gp.Ix,
                    iy=gp.Iy,
                    iz=gp.Iz,
                    wpx=gp.Wxmin,
                    wpy=gp.Wymin,
                    wpz=gp.Wzmin,
                    shy=gp.Shary,
                    shz=gp.Sharz,
                )

                # raise ValueError('Channel profiles currently not considered. Relevant for bm id "{}"'.format(s.id))

            elif s.type in SectionCat.general:
                gp = s.properties
                gens_str += gen.format(
                    id=s.id,
                    area=gp.Ax,
                    it=gp.Ix,
                    iy=gp.Iy,
                    iz=gp.Iz,
                    wpx=gp.Wxmin,
                    wpy=gp.Wymin,
                    wpz=gp.Wzmin,
                    shy=gp.Shary,
                    shz=gp.Sharz,
                )
            else:
                raise ValueError(f'Unknown section string "{s.type}"')

        return box_str + ip_str + tp_str + ang_str + cha_str + tub_str + circ_str + gens_str

    @property
    def create_usfos_set_str(self):
        """
        From USFOS documentation `GroupDef <http://usfos.no/manuals/usfos/users/documents/Usfos_UM_06.pdf#page=119>`_

        """
        from ada.core.utils import Counter, NewLine

        gr_ids = Counter(1)

        def create_groupdef_str(elset):
            """

            :param elset:
            :type elset: ada.fem.FemSet
            """

            if "include" not in elset.metadata.keys():
                return None
            if "unique_id" in elset.metadata.keys():
                gid = elset.metadata["unique_id"]
            else:
                gid = next(gr_ids)

            if "nonstru" in elset.metadata.keys():
                self._gnonstru.append(gid)

            nline = NewLine(10)
            mem_str = " ".join([f"{x.id}{next(nline)}" for x in elset.members])
            if elset.type == "elset":
                return f" Name  Group         {gid}  {elset.name}\n GroupDef            {gid}  Elem\n {mem_str}\n\n"
            else:
                return f" Name  Group         {gid}  {elset.name}\n GroupNod  {gid}  {mem_str}\n\n"

        gelset = "\n".join(
            filter(
                lambda x: x is not None,
                map(create_groupdef_str, self._gelsets.values()),
            )
        )
        gnset = "\n".join(filter(lambda x: x is not None, map(create_groupdef_str, self._gnsets.values())))
        return gelset + gnset

    @property
    def mass_str(self):
        def mstr(elem):
            """

            :param elem:
            :type elem: ada.fem.Elem
            """
            mass = elem.mass_props
            if mass.point_mass_type is not None or mass.point_mass_type == "anisotropic":
                raise ValueError("UsfosWriter currently only supports point masses")
            return f" NODEMASS       {elem.nodes[0].id}              {mass.mass:.3E}"

        header = "\n'            Node ID                             M A S S                \n"

        return header + "\n".join([mstr(m) for m in self.gelements.masses])

    @property
    def nonstru_str(self):
        from ada.core.utils import NewLine

        nonstru_str = """' Non Structural Elements\n NonStru Group"""
        nl = NewLine(10)

        def write_nonstru(g):
            return f" {g}" + next(nl)

        if len(self._gnonstru) > 0:
            return nonstru_str + "".join(list(map(write_nonstru, self._gnonstru)))
        else:
            return nonstru_str

    @property
    def eccent_str(self):
        eccent_str = "'             Ecc ID             ex             ey             ez\n"

        def write_eccent(data):
            """"""
            eid, e = data
            return f"ECCENT{eid:>12}{e[0]:>13.3f}{e[1]:>13.3f}{e[2]:>13.3f}"

        eccent_str += "\n".join(list(map(write_eccent, self._geccen)))
        return eccent_str


def materials_str(materials):
    """
    :return: Usfos material definition string
    """

    mat_str = """'            Mat ID     E-mod       Poiss     Yield      Density     ThermX\n"""

    def write_mat(i, m):
        """
        :param i: Index of material
        :param m: Material
        :type m: ada.Material
        """
        return " MISOIEP{:>11}{:>10.3E}{:>12}{:>10.3E}{:>13}{:>11}".format(
            i,
            m.model.E,
            m.model.v,
            float(m.model.sig_y),
            m.model.rho,
            m.model.alpha,
        )

    return mat_str + "\n".join(write_mat(i + 1, mat) for i, mat in enumerate(materials))


def shell_str(part):
    """

    :param part:
    :type part: ada.Part
    :return:
    """

    pl_str = "'            Elem ID      np1      np2      np3      np4    mater   geom      ec1    ec2    ec3    ec4\n"
    sec_str = """'            Geom ID     Thick"""
    geom_id = len(part.sections) + 1
    thick = []
    for el in sorted(part.fem.elements.shell, key=attrgetter("id")):
        t = el.fem_sec.thickness
        if t not in thick:
            thick.append(t)
            locid = thick.index(t)
            sec_str += "\n PLTHICK{:>12}{:>10}".format(locid + 1 + geom_id, t)

    sec_str += "\n"

    def write_elem(el):
        """

        :param el:
        :type el: ada.fem.Elem
        """
        t = el.fem_sec.thickness
        if len(el.nodes) > 4:
            raise ValueError(f'Shell id "{el.id}" consist of {len(el.nodes)} nodes')
        else:
            nodes_str = "".join(["{:>9}".format(no.id) for no in el.nodes])
            if len(el.nodes) == 3:
                return " TRISHELL{:>11}{}{:>9}{:>7}".format(
                    el.id,
                    nodes_str,
                    el.fem_sec.material.id,
                    thick.index(t) + 1 + geom_id,
                )
            else:
                return " QUADSHEL{:>11}{}{:>9}{:>7}".format(
                    el.id,
                    nodes_str,
                    el.fem_sec.material.id,
                    thick.index(t) + 1 + geom_id,
                )

    return sec_str + pl_str + "\n".join(list(map(write_elem, sorted(part.fem.elements.shell, key=attrgetter("id")))))
