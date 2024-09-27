from typing import TYPE_CHECKING

import numpy as np

from ada.config import Config, logger
from ada.fem import Bc, Connector, ConnectorSection, Constraint, Elem, FemSet

if TYPE_CHECKING:
    from ada import FEM, Assembly


def convert_ecc_to_mpc(fem: "FEM"):
    """Converts beam offsets to MPC constraints"""
    from ada import Node
    from ada.core.vector_utils import vector_length

    edited_nodes = dict()
    tol = Config().general_point_tol

    def build_constraint(n_old, elem, ecc, i):
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
                Constraint.TYPES.MPC,
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

    def build_mpc_for_end(elem, n_old, ecc, i):
        if n_old.id in edited_nodes.keys():
            build_constraint(n_old, elem, ecc, i)
        else:
            mat = np.eye(3)
            new_p = np.dot(mat, ecc) + n_old.p
            n_new = Node(new_p, parent=elem.parent)
            elem.parent.nodes.add(n_new, allow_coincident=True)
            m_set = FemSet(f"el{elem.id}_mpc{i + 1}_m", [n_new], "nset")
            s_set = FemSet(f"el{elem.id}_mpc{i + 1}_s", [n_old], "nset")
            c = Constraint(
                f"el{elem.id}_mpc{i + 1}_co",
                Constraint.TYPES.MPC,
                m_set,
                s_set,
                mpc_type="Beam",
                parent=elem.parent,
            )
            elem.parent.add_constraint(c)
            elem.nodes[i] = n_new
            edited_nodes[n_old.id] = n_new

    def build_mpc(elem: Elem):
        if elem.eccentricity.end1 is not None:
            n_old = elem.eccentricity.end1.node
            ecc = elem.eccentricity.end1.ecc_vector
            i = elem.nodes.index(n_old)
            build_mpc_for_end(elem, n_old, ecc, i)
        if elem.eccentricity.end2 is not None:
            n_old = elem.eccentricity.end2.node
            ecc = elem.eccentricity.end2.ecc_vector
            i = elem.nodes.index(n_old)
            build_mpc_for_end(elem, n_old, ecc, i)

    [build_mpc(el) for el in fem.elements.lines_ecc]


def convert_hinges_2_couplings(fem: "FEM"):
    """Convert beam hinges to coupling constraints"""
    from ada import Node
    from ada.core.utils import Counter
    from ada.fem.elements import Hinge

    constrain_ids = []

    max_node_id = fem.nodes.max_nid
    new_node_id = Counter(int(max_node_id + 10000))

    def convert_hinge(elem: Elem, hinge: Hinge):
        if hinge.constraint_ref is not None:
            return
        n = hinge.fem_node
        csys = hinge.csys
        d = hinge.retained_dofs

        n2 = Node(n.p, next(new_node_id), parent=elem.parent)
        elem.parent.nodes.add(n2, allow_coincident=True)
        i = elem.nodes.index(n)
        elem.nodes[i] = n2

        if elem.eccentricity is not None:
            if elem.eccentricity.end1 is not None:
                if n == elem.eccentricity.end1.node:
                    elem.eccentricity.end1.node = n2

            if elem.eccentricity.end2 is not None:
                if n == elem.eccentricity.end2.node:
                    elem.eccentricity.end2.node = n2

        if n2.id not in constrain_ids:
            constrain_ids.append(n2.id)
        else:
            logger.error(f"Hinged node {n2} cannot be added twice to different couplings")
            return None

        m_set = FemSet(f"el{elem.id}_hinge{i + 1}_m", [n], "nset")
        s_set = FemSet(f"el{elem.id}_hinge{i + 1}_s", [n2], "nset")

        elem.parent.add_set(m_set)
        elem.parent.add_set(s_set)
        c = Constraint(
            f"el{elem.id}_hinge{i + 1}_co",
            Constraint.TYPES.COUPLING,
            m_set,
            s_set,
            d,
            csys=csys,
        )
        elem.parent.add_constraint(c)
        hinge.constraint_ref = c
        logger.info(f"added constraint {c}")

    for el in fem.elements.lines_hinged:
        if el.hinge_prop.end1 is not None:
            convert_hinge(el, el.hinge_prop.end1)
        if el.hinge_prop.end2 is not None:
            convert_hinge(el, el.hinge_prop.end2)


def convert_springs_to_connectors(assembly: "Assembly"):
    """Converts all single noded springs to connector elements"""
    from ada import Node

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
