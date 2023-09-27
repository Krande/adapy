from operator import attrgetter

from ada import FEM, Part
from ada.config import logger
from ada.core.utils import Counter
from ada.fem import Elem


def shell_str(part: Part):
    pl_str = "'            Elem ID      np1      np2      np3      np4    mater   geom      ec1    ec2    ec3    ec4\n"
    sec_str = """'            Geom ID     Thick"""
    thick = []
    for fs in sorted(part.fem.sections.shells, key=attrgetter("id")):
        t = fs.thickness
        if t is None:
            raise ValueError("Thickness cannot be None")
        if fs not in thick:
            thick.append(fs)
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

    logger.info(
        "Note! Second order formulations of beam elements is not supported by Usfos beam. "
        "Will use regular beam formulation"
    )

    def write_elem(el: Elem) -> str:
        nonlocal locvecs
        n1 = el.nodes[0]
        n2 = el.nodes[1]
        fem_sec = el.fem_sec
        if fem_sec is None:
            raise ValueError(f"Element {el.id} is missing a fem section")
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
