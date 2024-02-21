from itertools import chain

from ada.api.containers import Materials
from ada.core.utils import roundoff
from ada.fem.formats.utils import str_to_int
from ada.materials import Material
from ada.materials.metals import CarbonSteel

from . import cards


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

    mat_names = {matid: mat_name for matid, mat_name in map(grab_name, cards.TDMATER.to_ff_re().finditer(bulk_str))}

    misosel = (get_mat(m, mat_names, part) for m in cards.MISOSEL.to_ff_re().finditer(bulk_str))
    morsmel = (get_morsmel(m, mat_names, part) for m in cards.MORSMEL.to_ff_re().finditer(bulk_str))
    return Materials(chain(misosel, morsmel), parent=part)


def grab_name(m):
    d = m.groupdict()
    return str_to_int(d["geo_no"]), d["name"]


def get_morsmel(m, mat_names, part) -> Material:
    """
    MORSMEL

    Anisotropy, Linear Elastic Structural Analysis, 2-D Membrane Elements and 2-D Thin Shell Elements

    :param m:
    :return:
    """

    d = m.groupdict()
    matno = str_to_int(d["matno"])

    mat_model = CarbonSteel(
        rho=roundoff(d["rho"]),
        E=roundoff(d["d11"]),
        v=roundoff(d["ps1"]),
        alpha=roundoff(d["alpha1"]),
        zeta=roundoff(d["damp1"]),
        sig_y=5e6,
    )

    return Material(name=mat_names[matno], mat_id=matno, mat_model=mat_model, metadata=d, parent=part)


def get_mat(match, mat_names, part) -> Material:
    d = match.groupdict()
    matno = str_to_int(d["matno"])
    mat_model = CarbonSteel(
        rho=roundoff(d["rho"]),
        E=roundoff(d["young"]),
        v=roundoff(d["poiss"]),
        alpha=roundoff(d["damp"]),
        zeta=roundoff(d["alpha"]),
        sig_y=roundoff(d["yield"]),
    )
    return Material(name=mat_names[matno], mat_id=matno, mat_model=mat_model, parent=part)
