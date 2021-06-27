from itertools import groupby
from operator import attrgetter

from ada.core.utils import create_guid
from ada.fem import ElemShapes


def to_ifc_fem(fem, f):
    """

    :param fem:
    :param f:
    :type fem: ada.fem.FEM
    :type f: ifcopenshell.file.file
    :return:
    """
    f.create_entity(
        "IfcStructuralAnalysisModel",
        create_guid(),
        f.by_type("IfcOwnerHistory")[0],
        fem.name,
        "ADA FEM model",
        ".NOTDEFINED.",
        "LOADING_3D",
    )

    for el_type, elements in groupby(fem.elements, key=attrgetter("type")):
        if el_type in ElemShapes.beam:
            for elem in elements:
                # TODO: add_fem_elem_beam(elem, parent)
                pass

    raise NotImplementedError()
