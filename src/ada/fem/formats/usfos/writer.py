import logging
import os
from operator import attrgetter

from ada import FEM, Assembly, Material, Node, Part
from ada.core.utils import Counter, NewLine, roundoff
from ada.fem import Bc, Elem, FemSet

from .write_profiles import sections_str


def to_fem(assembly: Assembly, name, analysis_dir=None, metadata=None):
    metadata = dict() if metadata is None else metadata
    parts = list(filter(lambda x: len(x.fem.nodes) > 0, assembly.get_all_subparts()))
    if len(parts) != 1:
        raise ValueError("Usfos writer currently only works for a single part")
    part = parts[0]

    head = """ HEAD\n\n\n"""
    eccen = []
    nonstrus = []

    with open(os.path.join(analysis_dir, r"ufo_bulk.fem"), "w") as d:
        d.write(head)
        d.write(nodal_str(part.fem) + "\n")
        d.write(beam_str(part.fem, eccen) + "\n")
        d.write(shell_str(part) + "\n")
        d.write(eccent_str(eccen) + "\n")
        d.write(sections_str(part.fem) + "\n")
        d.write(materials_str(part) + "\n")
        d.write(mass_str(part.fem) + "\n")
        d.write(create_usfos_set_str(part.fem, nonstrus) + "\n")

    control_file = metadata.get("control_file", None)
    if control_file is not None:
        with open(os.path.join(analysis_dir, r"usfos.fem"), "w") as d:
            d.write(control_file + "\n")
            d.write(nonstru_str(nonstrus) + "\n")

    print(f'Created an Usfos input deck at "{analysis_dir}"')


def create_usfos_set_str(fem: FEM, nonstrus):
    """USFOS documentation `GroupDef <https://usfos.no/manuals/usfos/users/documents/Usfos_UM_06.pdf#page=119>`_"""

    gr_ids = Counter(1)

    def create_groupdef_str(elset: FemSet):
        if "include" not in elset.metadata.keys():
            return None
        if "unique_id" in elset.metadata.keys():
            gid = elset.metadata["unique_id"]
        else:
            gid = next(gr_ids)

        if "nonstru" in elset.metadata.keys():
            nonstrus.append(gid)

        nline = NewLine(10)
        mem_str = " ".join([f"{x.id}{next(nline)}" for x in elset.members])
        if elset.type == "elset":
            return f" Name  Group         {gid}  {elset.name}\n GroupDef            {gid}  Elem\n {mem_str}\n\n"
        else:
            return f" Name  Group         {gid}  {elset.name}\n GroupNod  {gid}  {mem_str}\n\n"

    gelset = "\n".join(
        filter(
            lambda x: x is not None,
            map(create_groupdef_str, fem.elsets.values()),
        )
    )
    gnset = "\n".join(filter(lambda x: x is not None, map(create_groupdef_str, fem.nsets.values())))
    return gelset + gnset


def mass_str(fem: FEM):
    def mstr(elem: Elem):
        mass = elem.mass_props
        if mass.point_mass_type is not None or mass.point_mass_type == "anisotropic":
            raise ValueError("UsfosWriter currently only supports point masses")
        return f" NODEMASS       {elem.nodes[0].id}              {mass.mass:.3E}"

    header = "\n'            Node ID                             M A S S                \n"

    return header + "\n".join([mstr(m) for m in fem.elements.masses])


def nonstru_str(nonstru):
    from ada.core.utils import NewLine

    nonstru_str = """' Non Structural Elements\n NonStru Group"""
    nl = NewLine(10)

    def write_nonstru(g):
        return f" {g}" + next(nl)

    if len(nonstru) > 0:
        return nonstru_str + "".join(list(map(write_nonstru, nonstru)))
    else:
        return nonstru_str


def eccent_str(eccen):
    eccent_str = "'             Ecc ID             ex             ey             ez\n"

    def write_eccent(data):
        """

        :param data:
        :return:
        """
        eid, e = data
        return f"ECCENT{eid:>12}{e[0]:>13.3f}{e[1]:>13.3f}{e[2]:>13.3f}"

    eccent_str += "\n".join(list(map(write_eccent, eccen)))
    return eccent_str


def materials_str(part: Part):
    """Usfos material definition string"""

    mat_str = """'            Mat ID     E-mod       Poiss     Yield      Density     ThermX\n"""

    def write_mat(m: Material):
        return " MISOIEP{:>11}{:>10.3E}{:>12}{:>10.3E}{:>13}{:>11}".format(
            m.id,
            m.model.E,
            m.model.v,
            float(m.model.sig_y),
            roundoff(m.model.rho),
            m.model.alpha,
        )

    materials = {fs.material.id: fs.material for fs in part.fem.sections}
    return mat_str + "\n".join(write_mat(mat) for mat in materials.values())


def shell_str(part: Part):

    pl_str = "'            Elem ID      np1      np2      np3      np4    mater   geom      ec1    ec2    ec3    ec4\n"
    sec_str = """'            Geom ID     Thick"""
    thick = []
    for fs in sorted(part.fem.sections.shells, key=attrgetter("id")):
        t = fs.thickness
        if t is None:
            raise ValueError("Thickness cannot be None")
        if t not in thick:
            thick.append(t)
            sec_str += "\n PLTHICK{:>12}{:>10}".format(fs.id, t)

    sec_str += "\n"

    def write_elem(el: Elem):
        if len(el.nodes) > 4:
            raise ValueError(f'Shell id "{el.id}" consist of {len(el.nodes)} nodes')
        else:
            nodes_str = "".join(["{:>9}".format(no.id) for no in el.nodes])
            if len(el.nodes) == 3:
                return f" TRISHELL{el.id:>11}{nodes_str}{'':>9}{el.fem_sec.material.id:>9}{el.fem_sec.id:>7}"
            else:
                return f" QUADSHEL{el.id:>11}{nodes_str}{el.fem_sec.material.id:>9}{el.fem_sec.id:>7}"

    return sec_str + pl_str + "\n".join(list(map(write_elem, sorted(part.fem.elements.shell, key=attrgetter("id")))))


def nodal_str(fem: FEM) -> str:
    node_str = "'            Node ID            X              Y              Z    Boundary code\n"
    f = " NODE {nid:>15} {x:>13.3f} {y:>13.3f} {z:>13.3f}{bc}"

    def write_bc(bc: Bc):
        bcs_str = ""
        for dof in range(1, 7):
            if dof in bc.dofs:
                bcs_str += " 1"
            else:
                bcs_str += " 0"

        return bcs_str

    def write_node(no: Node):
        bc_str = "" if no.bc is None else write_bc(no.bc)
        return f.format(nid=no.id, x=no[0], y=no[1], z=no[2], bc=bc_str)

    return (
        node_str + "\n".join(list(map(write_node, sorted(fem.nodes, key=attrgetter("id"))))).rstrip()
        if len(fem.nodes) > 0
        else "** No Nodes"
    )


def beam_str(fem: FEM, eccen):
    """

    # USFOS Strings

    # Beam String
    '            Elem ID     np1      np2   material   geom    lcoor    ecc1    ecc2
    BEAM            1127     1343     1344        1        1       1

    # Unit Vector String
    '            Loc-Coo           dx             dy             dz
    UNITVEC    60000001        0.00000        0.00000        1.00000

    """
    locvecs = []
    eccen_counter = Counter(1)
    loc_str = "'\n'            Loc-Coo           dx             dy             dz\n"
    bm_str = "'\n'            Elem ID     np1      np2   material   geom    lcoor    ecc1    ecc2\n"

    logging.info(
        "Note! Second order formulations of beam elements is not supported by Usfos beam. "
        "Will use regular beam formulation"
    )

    def write_elem(el: Elem) -> str:
        nonlocal locvecs
        n1 = el.nodes[0]
        n2 = el.nodes[1]
        fem_sec = el.fem_sec
        mat = fem_sec.material
        xvec = fem_sec.local_z
        xvec_str = f"{xvec[0]:>13.5f}{xvec[1]:>15.5f}{xvec[2]:>15.5f}"

        if xvec_str in locvecs:
            locid = locvecs.index(xvec_str)
        else:
            locvecs.append(xvec_str)
            locid = locvecs.index(xvec_str)

        if el.eccentricity is not None:
            ecc1_str = " 0"
            ecc2_str = " 0"
            if el.eccentricity.end1 is not None:
                ecc1 = next(eccen_counter)
                eccen.append((ecc1, el.eccentricity.end1.ecc_vector))
                ecc1_str = f" {ecc1}"
            if el.eccentricity.end2 is not None:
                ecc2 = next(eccen_counter)
                eccen.append((ecc2, el.eccentricity.end2.ecc_vector))
                ecc2_str = f" {ecc2}"
        else:
            ecc1_str = ""
            ecc2_str = ""
        return f" BEAM{el.id:>15}{n1.id:>8}{n2.id:>9}{mat.id:>11}{el.fem_sec.id:>7}{locid + 1:>9}{ecc1_str}{ecc2_str}"

    bm_str += "\n".join(list(map(write_elem, fem.elements.lines)))

    for i, loc in enumerate(locvecs):
        loc_str += " UNITVEC{:>13}{:<10}\n".format(i + 1, loc)

    return bm_str + "\n" + loc_str
