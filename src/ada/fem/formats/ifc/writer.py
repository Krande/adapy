import ifcopenshell

from ada import FEM
from ada.cadit.ifc.utils import (
    create_ifc_placement,
    create_local_placement,
    create_reference_subrep,
)
from ada.cadit.ifc.write.geom.points import cpt
from ada.config import logger
from ada.core.guid import create_guid
from ada.core.utils import to_real
from ada.fem import Elem

from .helper_utils import ifc_vertex


def to_ifc_fem(fem: FEM, f: ifcopenshell.file) -> None:
    owner_history = fem.parent.get_assembly().ifc_store.owner_history
    f.create_entity(
        "IfcStructuralAnalysisModel",
        create_guid(),
        owner_history,
        fem.name,
        "ADA FEM model",
        ".NOTDEFINED.",
        "LOADING_3D",
    )
    subref = create_reference_subrep(f, create_ifc_placement(f))
    el_ids = []

    logger.warning("Note! IFC FEM export is Work in progress")

    for elem in fem.elements:
        if elem.id not in el_ids:
            el_ids.append(elem.id)
        else:
            logger.error(f'Skipping doubly defined element "{elem.id}"')
            continue

        if elem.shape.elem_type_group == elem.EL_TYPES.LINE:
            _ = line_elem_to_ifc(elem, f, subref, owner_history)
        elif elem.shape.elem_type_group == elem.EL_TYPES.SHELL:
            _ = shell_elem_to_ifc(elem, f, subref, owner_history)
        else:
            logger.error(f'Unsupported elem type "{elem.type}"')


def shell_elem_to_ifc(elem: Elem, f, subref, owner_history):
    verts = [ifc_vertex(point, f) for point in elem.nodes]

    orientedEdges = []
    for e1, e2 in elem.shape.edges_seq:
        orientedEdges.append(f.createIfcOrientedEdge(None, None, f.createIfcEdge(verts[e1], verts[e2]), True))

    edgeLoop = f.createIfcEdgeLoop(tuple(orientedEdges))
    plane = f.create_entity(
        "IfcPlane", create_ifc_placement(f, to_real(elem.fem_sec.local_z), to_real(elem.fem_sec.local_x))
    )
    faceBound = f.createIfcFaceBound(edgeLoop, True)
    face = f.createIfcFaceSurface((faceBound,), plane, True)
    faceTopologyRep = f.createIfcTopologyRepresentation(subref["reference"], "Reference", "Face", (face,))
    faceProdDefShape = f.createIfcProductDefinitionShape(None, None, (faceTopologyRep,))

    return f.create_entity(
        "IfcStructuralSurfaceMember",
        create_guid(),
        owner_history,
        f"El{elem.name}",
        None,
        None,
        create_local_placement(f),
        faceProdDefShape,
        "SHELL",
        elem.fem_sec.thickness,
    )


def line_elem_to_ifc(elem: Elem, f, subref, owner_history):
    """

    :param elem:
    :param f:
    :param owner_history:
    :type f: ifcopenshell.file.file
    :return:
    """

    local_z = f.createIfcDirection(to_real(elem.fem_sec.local_z))
    p1 = elem.nodes[0].p
    p2 = elem.nodes[-1].p
    edge = f.createIfcEdge(f.createIfcVertexPoint(cpt(f, p1)), f.createIfcVertexPoint(cpt(f, p2)))

    edge_topology_rep = f.createIfcTopologyRepresentation(subref["reference"], "Reference", "Edge", (edge,))
    edge_prod_def_shape = f.create_entity("IfcProductDefinitionShape", None, None, (edge_topology_rep,))
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
