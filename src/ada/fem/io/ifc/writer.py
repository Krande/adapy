import logging
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
    from ada.core.ifc_utils import create_global_axes, create_reference_subrep

    owner_history = f.by_type("IfcOwnerHistory")[0]
    f.create_entity(
        "IfcStructuralAnalysisModel",
        create_guid(),
        owner_history,
        fem.name,
        "ADA FEM model",
        ".NOTDEFINED.",
        "LOADING_3D",
    )
    subref = create_reference_subrep(f, create_global_axes(f))
    for el_type, elements in groupby(fem.elements, key=attrgetter("type")):
        if el_type in ElemShapes.beam:
            for elem in elements:
                elem_to_ifc(elem, f, subref)

    logging.error("Note! IFC FEM export is Work in progress")


def elem_to_ifc(elem, f, subref):
    """

    :param elem:
    :param f:
    :type elem: ada.fem.Elem
    :type f: ifcopenshell.file.file
    :return:
    """
    from ada.core.ifc_utils import create_local_placement, ifc_p, to_real

    local_z = f.createIfcDirection(to_real(elem.fem_sec.local_z))
    p1 = elem.nodes[0].p
    p2 = elem.nodes[-1].p
    edge = f.createIfcEdge(f.createIfcVertexPoint(ifc_p(f, p1)), f.createIfcVertexPoint(ifc_p(f, p2)))

    edge_topology_rep = f.createIfcTopologyRepresentation(subref["reference"], "Reference", "Edge", (edge,))
    edge_prod_def_shape = f.createIfcProductDefinitionShape(None, None, (edge_topology_rep,))
    owner_history = f.by_type("IfcOwnerHistory")[0]
    ifc_stru_member = f.create_entity(
        "IfcStructuralCurveMember",
        create_guid(),
        owner_history,
        f"E{elem.name}",
        None,
        None,
        create_local_placement(f),
        edge_prod_def_shape,
        "RIGID_JOINED_MEMBER",
        local_z,
    )

    return ifc_stru_member
