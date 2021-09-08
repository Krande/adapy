from typing import List, Union

import numpy as np

from ada import FEM, Assembly, Beam, Node, Part
from ada.config import Settings
from ada.core.utils import vector_length
from ada.fem import (
    Bc,
    Connector,
    ConnectorSection,
    Constraint,
    Elem,
    FemSection,
    FemSet,
)

from .shapes import ElemShapes, ElemType


def get_eldata(fem_source: Union[Assembly, Part, FEM]):
    """Return a dictionary of basic mesh statistics"""

    el_types = dict()

    def scan_elem(mesh):
        for el in mesh.elements:
            if el.type not in el_types.keys():
                el_types[el.type] = 1
            else:
                el_types[el.type] += 1

    if type(fem_source) is Assembly:
        for p in fem_source.parts.values():
            scan_elem(p.fem)
    elif issubclass(type(fem_source), Part):
        scan_elem(fem_source.fem)
    elif type(fem_source) is FEM:
        scan_elem(fem_source)
    else:
        raise ValueError(f'Unknown fem_source "{fem_source}"')
    return el_types


def convert_springs_to_connectors(assembly: Assembly):
    """Converts all single noded springs to connector elements"""
    for p in assembly.get_all_subparts():
        for spring in p.fem.springs.values():
            n1 = spring.nodes[0]
            n2 = Node(n1.p - np.array([0, 0, 10e-3]))
            assembly.fem.add_rp(spring.name + "_rp", n2)
            fs = FemSet(spring.name + "_bc", [n2], "nset")
            assembly.fem.add_set(fs)
            assembly.fem.add_bc(Bc(spring.name + "_bc", fs, [1, 2, 3, 4, 5, 6]))
            diag = []
            for dof, row in enumerate(spring.stiff):
                for j, stiffness in enumerate(row):
                    if dof == j:
                        diag.append(stiffness)

            con_sec = ConnectorSection(spring.name + "_consec", diag, [])
            assembly.fem.add_connector_section(con_sec)
            con = Connector(spring.name + "_con", spring.id, n1, n2, "bushing", con_sec)
            assembly.fem.add_connector(con)
        p.fem._springs = dict()
        p.fem.elements.filter_elements(delete_elem=["SPRING1"])


def get_beam_end_nodes(bm: Beam, end=1, tol=1e-3) -> List[Node]:
    """Get list of nodes from end of beam"""
    p = bm.parent
    nodes = p.fem.nodes
    w = bm.section.w_btn
    h = bm.section.h
    xv = np.array(bm.xvec)
    yv = np.array(bm.yvec)
    zv = np.array(bm.up)

    n1_min = bm.n1.p - xv * tol - (h / 2 + tol) * zv - (w / 2 + tol) * yv
    n1_max = bm.n1.p + xv * tol + (h / 2 + tol) * zv + (w / 2 + tol) * yv
    members = [e for e in nodes.get_by_volume(n1_min, n1_max)]
    return members


def is_line_elem(elem: Elem):

    return True if elem.type in ElemShapes.lines else False


def convert_ecc_to_mpc(fem: FEM):
    """Converts beam offsets to MPC constraints"""
    edited_nodes = dict()
    tol = Settings.point_tol

    def build_mpc(fs: FemSection):
        if fs.offset is None or fs.type != ElemType.LINE:
            return
        elem = fs.elset.members[0]
        for n_old, ecc in fs.offset:
            i = elem.nodes.index(n_old)
            if n_old.id in edited_nodes.keys():
                n_new = edited_nodes[n_old.id]
                mat = np.eye(3)
                new_p = np.dot(mat, ecc) + n_old.p
                n_new_ = Node(new_p, parent=elem.parent)
                if vector_length(n_new_.p - n_new.p) > tol:
                    elem.parent.nodes.add(n_new_, allow_coincident=True)
                    m_set = FemSet(f"el{elem.id}_mpc{i + 1}_m", [n_new_], "nset")
                    s_set = FemSet(f"el{elem.id}_mpc{i + 1}_s", [n_old], "nset")
                    c = Constraint(
                        f"el{elem.id}_mpc{i + 1}_co",
                        "mpc",
                        m_set,
                        s_set,
                        mpc_type="Beam",
                        parent=elem.parent,
                    )
                    elem.parent.add_constraint(c)
                    elem.nodes[i] = n_new_
                    edited_nodes[n_old.id] = n_new_

                else:
                    elem.nodes[i] = n_new
                    edited_nodes[n_old.id] = n_new
            else:
                mat = np.eye(3)
                new_p = np.dot(mat, ecc) + n_old.p
                n_new = Node(new_p, parent=elem.parent)
                elem.parent.nodes.add(n_new, allow_coincident=True)
                m_set = FemSet(f"el{elem.id}_mpc{i + 1}_m", [n_new], "nset")
                s_set = FemSet(f"el{elem.id}_mpc{i + 1}_s", [n_old], "nset")
                c = Constraint(
                    f"el{elem.id}_mpc{i + 1}_co",
                    "mpc",
                    m_set,
                    s_set,
                    mpc_type="Beam",
                    parent=elem.parent,
                )
                elem.parent.add_constraint(c)

                elem.nodes[i] = n_new
                edited_nodes[n_old.id] = n_new

    list(map(build_mpc, filter(lambda x: x.offset is not None, fem.sections)))


def convert_hinges_2_couplings(fem: FEM):
    """
    Convert beam hinges to coupling constraints
    """

    def converthinges(fs: FemSection):
        if fs.hinges is None or fs.type != ElemType.LINE:
            return
        elem = fs.elset.members[0]
        assert isinstance(elem, Elem)

        for n, d, csys in fs.hinges:
            n2 = Node(n.p, None, parent=elem.parent)
            elem.parent.nodes.add(n2, allow_coincident=True)
            i = elem.nodes.index(n)
            elem.nodes[i] = n2
            if elem.fem_sec.offset is not None:
                if n in [x[0] for x in elem.fem_sec.offset]:
                    elem.fem_sec.offset[i] = (n2, elem.fem_sec.offset[i][1])

            s_set = FemSet(f"el{elem.id}_hinge{i + 1}_s", [n], "nset")
            m_set = FemSet(f"el{elem.id}_hinge{i + 1}_m", [n2], "nset")
            elem.parent.add_set(m_set)
            elem.parent.add_set(s_set)
            c = Constraint(
                f"el{elem.id}_hinge{i + 1}_co",
                "coupling",
                m_set,
                s_set,
                d,
                csys=csys,
            )
            elem.parent.add_constraint(c)

    list(map(converthinges, filter(lambda x: x.hinges is not None, fem.sections)))
